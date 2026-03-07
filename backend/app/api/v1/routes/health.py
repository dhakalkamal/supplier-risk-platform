"""Health and readiness routes — no authentication required.

GET /health  — liveness probe: always 200 if the process is alive
GET /ready   — readiness probe: 200 when Postgres + Redis + Kafka are reachable,
               503 when any dependency is down
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.models.responses import (
    DataResponse,
    DependencyStatus,
    HealthResponse,
    ReadinessResponse,
)

log = structlog.get_logger()
router = APIRouter(tags=["health"])

_VERSION = "1.0.0"


@router.get("/health", response_model=DataResponse[HealthResponse])
async def health() -> DataResponse[HealthResponse]:
    """Liveness probe — always returns 200 when the process is running."""
    return DataResponse(
        data=HealthResponse(
            status="ok",
            version=_VERSION,
            timestamp=datetime.now(tz=timezone.utc),
        )
    )


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe — checks Postgres, Redis, and Kafka."""
    postgres_ok = await _check_postgres()
    redis_ok = await _check_redis(request)
    kafka_ok = await _check_kafka(request)

    all_ready = postgres_ok and redis_ok and kafka_ok
    status_code = 200 if all_ready else 503

    body = DataResponse(
        data=ReadinessResponse(
            status="ready" if all_ready else "not_ready",
            dependencies=DependencyStatus(
                postgres="ok" if postgres_ok else "unavailable",
                redis="ok" if redis_ok else "unavailable",
                kafka="ok" if kafka_ok else "unavailable",
            ),
            timestamp=datetime.now(tz=timezone.utc),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


async def _check_postgres() -> bool:
    """Ping Postgres via the module-level asyncpg pool."""
    try:
        from backend.app.db.connection import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False


async def _check_redis(request: Request) -> bool:
    """Ping Redis via app.state.redis."""
    try:
        redis = getattr(getattr(request, "app", None), "state", None)
        redis = getattr(redis, "redis", None) if redis else None
        if redis is None:
            return False
        await redis.ping()
        return True
    except Exception:
        return False


async def _check_kafka(request: Request) -> bool:
    """Check Kafka broker reachability via a raw TCP connect (2-second timeout)."""
    try:
        from backend.app.config import get_settings

        settings = get_settings()
        broker = settings.kafka_bootstrap_servers.split(",")[0].strip()
        host, port_str = broker.rsplit(":", 1)
        sock = socket.create_connection((host, int(port_str)), timeout=2)
        sock.close()
        return True
    except Exception:
        return False
