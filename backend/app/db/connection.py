"""asyncpg connection pool factory and FastAPI dependency.

Usage:
    # In FastAPI startup handler:
    await create_pool(get_settings())

    # In route handlers (via Depends):
    pool: asyncpg.Pool = Depends(get_pool)

The pool is module-level state, initialised once at startup and closed at shutdown.
Never instantiate a pool directly in route handlers or repositories.
"""

from __future__ import annotations

import asyncpg
import structlog

from backend.app.config import Settings

log = structlog.get_logger()

_pool: asyncpg.Pool | None = None


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Create the asyncpg connection pool and store it as module-level state.

    Called once during FastAPI startup. Raises if the DB is unreachable.
    """
    global _pool
    dsn = (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        server_settings={"application_name": "supplier_risk_api"},
    )
    log.info(
        "db.pool.created",
        host=settings.postgres_host,
        port=settings.postgres_port,
        db=settings.postgres_db,
        min_size=2,
        max_size=10,
    )
    return _pool


async def close_pool() -> None:
    """Close the connection pool. Called during FastAPI shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db.pool.closed")


async def get_pool() -> asyncpg.Pool:
    """FastAPI dependency — return the active asyncpg pool.

    Raises RuntimeError if called before create_pool() (i.e. before startup).
    Inject via:
        pool: asyncpg.Pool = Depends(get_pool)
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Ensure create_pool() is called in the FastAPI startup handler."
        )
    return _pool
