"""FastAPI application factory for the Supplier Risk Intelligence Platform."""

import structlog
from fastapi import FastAPI

log = structlog.get_logger()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns a configured FastAPI instance with all routers, middleware,
    and event handlers attached. Import and call this in uvicorn:
        uvicorn backend.app.main:app --reload
    """
    app = FastAPI(
        title="Supplier Risk Intelligence Platform",
        description="Real-time supplier health monitoring and early-warning risk scoring.",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint for load balancer and Kubernetes readiness probe."""
        return {"status": "ok"}

    @app.on_event("startup")
    async def on_startup() -> None:
        log.info("app.startup", version="0.1.0")

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        log.info("app.shutdown")

    return app


app = create_app()
