"""FastAPI shared dependencies.

All dependencies here are injected via Depends() — never called directly in routes.

Key exports:
    TenantContext        — Pydantic model carrying authenticated tenant/user info
    get_current_tenant   — JWT → TenantContext; raises 401 on invalid/missing token
    get_db_pool          — returns the asyncpg connection pool
    require_admin        — like get_current_tenant but raises 403 for viewer role

JWT validation uses python-jose against Auth0 JWKS.
JWKS are cached in memory for 1 hour to avoid per-request round-trips to Auth0.

Dev bypass: when settings.auth0_domain is empty (local dev without Auth0),
get_current_tenant returns a synthetic admin TenantContext. Tests override this
dependency directly via app.dependency_overrides.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import asyncpg
import httpx
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from backend.app.config import Settings, get_settings
from backend.app.db import connection

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# JWKS cache — module-level; lives for the lifetime of the process
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0
_JWKS_CACHE_TTL_SECONDS: int = 3600  # 1 hour


async def _fetch_jwks(settings: Settings) -> dict[str, Any]:
    """Fetch Auth0 JWKS, using the in-memory cache when fresh."""
    global _jwks_cache, _jwks_fetched_at

    cache_age = time.monotonic() - _jwks_fetched_at
    if _jwks_cache is not None and cache_age < _JWKS_CACHE_TTL_SECONDS:
        return _jwks_cache

    jwks_url = f"https://{settings.auth0_domain}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(jwks_url)
        response.raise_for_status()
        fresh: dict[str, Any] = response.json()
        _jwks_cache = fresh
        _jwks_fetched_at = time.monotonic()
        log.info("auth.jwks.refreshed", domain=settings.auth0_domain)

    return _jwks_cache


def _decode_jwt(token: str, jwks: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Verify a JWT against Auth0 JWKS and return its claims.

    Raises HTTPException(401) on any verification failure.
    Accepts tokens up to 30 seconds past expiry (clock skew tolerance).
    """
    try:
        unverified_header: dict[str, Any] = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Malformed token header: {exc}")

    kid = unverified_header.get("kid")
    matching_key = next(
        (key for key in jwks.get("keys", []) if key.get("kid") == kid),
        None,
    )
    if matching_key is None:
        raise HTTPException(
            status_code=401,
            detail=f"Token signing key {kid!r} not found in JWKS. Key may have rotated.",
        )

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            matching_key,
            algorithms=["RS256"],
            audience=settings.auth0_audience,
            issuer=f"https://{settings.auth0_domain}/",
            options={"leeway": 30},  # 30-second clock skew tolerance
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    return payload


# ---------------------------------------------------------------------------
# TenantContext
# ---------------------------------------------------------------------------


class TenantContext(BaseModel):
    """Authenticated caller's tenant and role, extracted from the JWT.

    Injected into every protected route handler via get_current_tenant().
    The tenant_id is the authoritative source — never accept it from request
    body or URL params (API_SPEC.md Section 1, ARCHITECTURE.md Section 15).
    """

    tenant_id: str
    user_id: str
    role: Literal["admin", "viewer"]
    plan: Literal["starter", "growth", "pro", "enterprise"]
    email: str


# ---------------------------------------------------------------------------
# OAuth2 bearer scheme (auto_error=False so we can return a clean 401)
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


# ---------------------------------------------------------------------------
# Core dependencies
# ---------------------------------------------------------------------------


async def get_current_tenant(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> TenantContext:
    """Validate the JWT and return the authenticated TenantContext.

    Raises:
        HTTPException(401): missing, expired, or invalid token
    """
    # Dev bypass — when Auth0 is not configured, return a synthetic admin tenant.
    # Tests override this dependency directly via app.dependency_overrides.
    if not settings.auth0_domain:
        log.debug("auth.dev_bypass", reason="auth0_domain not configured")
        return TenantContext(
            tenant_id="dev-tenant-00000000-0000-0000-0000-000000000000",
            user_id="dev-user-00000000-0000-0000-0000-000000000000",
            role="admin",
            plan="enterprise",
            email="dev@localhost",
        )

    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use: Authorization: Bearer <token>",
        )

    jwks = await _fetch_jwks(settings)
    payload = _decode_jwt(token, jwks, settings)

    required_claims = ("tenant_id", "role", "plan")
    missing = [c for c in required_claims if c not in payload]
    if missing:
        raise HTTPException(
            status_code=401,
            detail=f"Token is missing required claims: {missing}",
        )

    return TenantContext(
        tenant_id=payload["tenant_id"],
        user_id=payload["sub"],
        role=payload["role"],
        plan=payload["plan"],
        email=payload.get("email", ""),
    )


async def get_db_pool() -> asyncpg.Pool:
    """Return the active asyncpg connection pool.

    Raises RuntimeError if called before startup (pool not yet initialised).
    Inject via:
        pool: asyncpg.Pool = Depends(get_db_pool)
    """
    return await connection.get_pool()


async def require_admin(
    tenant: TenantContext = Depends(get_current_tenant),
) -> TenantContext:
    """Like get_current_tenant, but raises 403 if the caller is not an admin.

    Use on any endpoint that requires the admin role:
        tenant: TenantContext = Depends(require_admin)
    """
    if tenant.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="This action requires admin role.",
        )
    return tenant


# ---------------------------------------------------------------------------
# Redis dependency
# ---------------------------------------------------------------------------


async def get_redis_client(request: Request) -> Any:
    """Return the async Redis client from app.state.

    The client is created at startup in main.py and stored on app.state.redis.
    """
    redis = getattr(getattr(request, "app", None), "state", None)
    redis = getattr(redis, "redis", None) if redis else None
    if redis is None:
        raise RuntimeError("Redis client not initialized. Ensure startup() ran.")
    return redis


# ---------------------------------------------------------------------------
# Repository factory dependencies
# Injected into route handlers via Depends() — never instantiate directly.
# ---------------------------------------------------------------------------


async def get_supplier_repository(
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> "SupplierRepository":
    from backend.app.repositories.supplier_repository import PostgresSupplierRepository

    return PostgresSupplierRepository(pool)


async def get_score_repository(
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> "ScoreRepository":
    from backend.app.repositories.score_repository import PostgresScoreRepository

    return PostgresScoreRepository(pool)


async def get_alert_repository(
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> "AlertRepository":
    from backend.app.repositories.alert_repository import PostgresAlertRepository

    return PostgresAlertRepository(pool)


async def get_news_repository(
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> "NewsRepository":
    from backend.app.repositories.news_repository import PostgresNewsRepository

    return PostgresNewsRepository(pool)


async def get_settings_repository(
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> "SettingsRepository":
    from backend.app.repositories.settings_repository import PostgresSettingsRepository

    return PostgresSettingsRepository(pool)


# ---------------------------------------------------------------------------
# TYPE_CHECKING-only imports (avoid circular imports at runtime)
# ---------------------------------------------------------------------------

if False:  # TYPE_CHECKING equivalent without the import
    from backend.app.repositories.alert_repository import AlertRepository
    from backend.app.repositories.news_repository import NewsRepository
    from backend.app.repositories.score_repository import ScoreRepository
    from backend.app.repositories.settings_repository import SettingsRepository
    from backend.app.repositories.supplier_repository import SupplierRepository
