"""Celery application factory.

Broker: Redis (DB 0)
Result backend: Redis (DB 0)
Three queues: scoring, notifications, websocket

Never use DB 1 in tests — that is reserved for test isolation.
"""

from __future__ import annotations

from celery import Celery

from backend.app.config import get_settings


def create_celery_app() -> Celery:
    """Create and configure the Celery application."""
    cfg = get_settings()

    app = Celery("supplier_risk")

    app.conf.update(
        broker_url=cfg.redis_url,
        result_backend=cfg.redis_url,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Reliability settings
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # Queue definitions
        task_queues={
            "scoring": {},
            "notifications": {},
            "websocket": {},
        },
        task_default_queue="scoring",
        # Task routing
        task_routes={
            "backend.app.worker.tasks.dispatch_email_alert": {"queue": "notifications"},
            "backend.app.worker.tasks.dispatch_slack_alert": {"queue": "notifications"},
            "backend.app.worker.tasks.push_websocket_event": {"queue": "websocket"},
        },
    )

    return app


celery_app = create_celery_app()
