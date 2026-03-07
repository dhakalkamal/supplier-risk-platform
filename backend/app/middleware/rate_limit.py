"""RateLimitMiddleware — Redis sliding-window rate limiter.

Limit: 1000 requests/minute per tenant_id.
Exempt: /health and /ready (liveness/readiness probes).

Headers added to every response:
  X-RateLimit-Limit:     1000
  X-RateLimit-Remaining: <remaining>
  X-RateLimit-Reset:     <unix epoch when window resets>

On breach:
  HTTP 429 with RATE_LIMITED error envelope and Retry-After header.

tenant_id is extracted from the JWT payload WITHOUT signature verification —
safe for rate-limiting purposes (not a security decision).
Full auth validation happens inside route dependencies.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = structlog.get_logger()

_RATE_LIMIT = 1000
_WINDOW_SECONDS = 60
_EXEMPT_PATHS = {"/health", "/ready"}


def _extract_tenant_id(request: Request) -> str | None:
    """Decode JWT payload without signature verification to get tenant_id."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        claims: dict[str, Any] = jwt.get_unverified_claims(token)
        return claims.get("tenant_id")
    except Exception:
        return None


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        tenant_id = _extract_tenant_id(request)
        if tenant_id is None:
            # No valid token — pass through; auth middleware will reject it
            return await call_next(request)

        redis = getattr(getattr(request, "app", None), "state", None)
        redis = getattr(redis, "redis", None) if redis else None

        # If Redis is unavailable, skip rate limiting (degrade gracefully)
        if redis is None:
            return await call_next(request)

        try:
            remaining, reset_at = await _check_rate_limit(redis, tenant_id)
        except Exception as exc:
            log.warning("rate_limit.redis_error", error=str(exc))
            return await call_next(request)

        limit_headers = {
            "X-RateLimit-Limit": str(_RATE_LIMIT),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "X-RateLimit-Reset": str(reset_at),
        }

        if remaining < 0:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": f"Rate limit exceeded. {_RATE_LIMIT} requests/minute allowed.",
                        "request_id": _request_id(request),
                        "details": {
                            "retry_after_seconds": str(_WINDOW_SECONDS),
                            "limit": str(_RATE_LIMIT),
                            "window": f"{_WINDOW_SECONDS}s",
                        },
                    }
                },
                headers={**limit_headers, "Retry-After": str(_WINDOW_SECONDS)},
            )

        response = await call_next(request)
        for key, value in limit_headers.items():
            response.headers[key] = value
        return response


async def _check_rate_limit(redis: Any, tenant_id: str) -> tuple[int, int]:
    """Sliding-window counter using a Redis sorted set.

    Returns (remaining_requests, reset_epoch_seconds).
    remaining < 0 means the limit was exceeded.
    """
    now = time.time()
    window_start = now - _WINDOW_SECONDS
    reset_at = int(now) + _WINDOW_SECONDS
    key = f"rate_limit:{tenant_id}"
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, _WINDOW_SECONDS + 1)
    results = await pipe.execute()

    count: int = results[2]
    remaining = _RATE_LIMIT - count
    return remaining, reset_at
