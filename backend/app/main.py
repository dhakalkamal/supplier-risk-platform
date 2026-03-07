"""FastAPI application factory for the Supplier Risk Intelligence Platform."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.v1 import api_v1_router
from backend.app.api.v1.routes.health import router as health_router
from backend.app.api.v1.routes.websocket import router as websocket_router
from backend.app.config import get_settings
from backend.app.db.connection import close_pool, create_pool
from backend.app.db.redis_client import close_redis, create_redis
from backend.app.middleware.error_handler import register_exception_handlers
from backend.app.middleware.rate_limit import RateLimitMiddleware
from backend.app.middleware.request_id import RequestIDMiddleware
from backend.app.services.websocket_manager import websocket_manager

log = structlog.get_logger()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Middleware is added in outermost-last order (Starlette add_middleware is LIFO):
      Request flow: RequestIDMiddleware → RateLimitMiddleware → CORSMiddleware → routes
    """
    settings = get_settings()

    app = FastAPI(
        title="Supplier Risk Intelligence Platform",
        description="Real-time supplier health monitoring and early-warning risk scoring.",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    # Middleware — last add_middleware call is outermost (runs first on request)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIDMiddleware)  # outermost: assigns X-Request-ID first

    # Domain exception → HTTP response mappings
    register_exception_handlers(app)

    # Routers
    app.include_router(health_router)
    app.include_router(api_v1_router, prefix="/api/v1")
    app.include_router(websocket_router, prefix="/api/v1")

    @app.on_event("startup")
    async def on_startup() -> None:
        cfg = get_settings()
        app.state.db_pool = await create_pool(cfg)
        try:
            app.state.redis = await create_redis(cfg)
            websocket_manager.set_redis(app.state.redis)
            asyncio.create_task(websocket_manager.start_redis_listener())
        except Exception as exc:
            log.warning("redis.startup_failed", error=str(exc))
            app.state.redis = None
        log.info("app.startup", version="1.0.0")

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await close_pool()
        redis = getattr(app.state, "redis", None)
        if redis is not None:
            await close_redis(redis)
        log.info("app.shutdown")

    return app


app = create_app()
