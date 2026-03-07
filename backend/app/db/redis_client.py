"""Async Redis client factory and FastAPI dependency.

Usage:
    # In FastAPI startup:
    app.state.redis = await create_redis(get_settings())

    # In route handlers / middleware via request.app.state:
    redis = request.app.state.redis

    # Via Depends() in routes:
    redis: Redis = Depends(get_redis_client)
"""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog

from backend.app.config import Settings

log = structlog.get_logger()

Redis = aioredis.Redis  # re-export for type hints


async def create_redis(settings: Settings) -> aioredis.Redis:
    """Create and return an async Redis client. Called once at startup."""
    client: aioredis.Redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    await client.ping()  # type: ignore[misc]
    log.info("redis.connected", url=settings.redis_url)
    return client


async def close_redis(client: aioredis.Redis) -> None:
    """Close the Redis client. Called at shutdown."""
    await client.aclose()
    log.info("redis.disconnected")
