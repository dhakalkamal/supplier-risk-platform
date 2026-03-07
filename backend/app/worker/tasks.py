"""Celery tasks for alert dispatch.

Tasks are thin — all logic lives in services.
Every task logs with structlog. Every task handles its own exceptions
and never raises — a failed notification should not crash the worker.

Retry policy:
  - dispatch_email_alert:  3 retries, exponential backoff (2^n * 60s)
  - dispatch_slack_alert:  3 retries, exponential backoff
  - push_websocket_event:  2 retries, fixed 30s delay
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from backend.app.worker.celery_app import celery_app

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Email dispatch
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="backend.app.worker.tasks.dispatch_email_alert",
    queue="notifications",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def dispatch_email_alert(self: Any, alert_id: str, tenant_id: str) -> None:
    """Send alert email via SendGrid.

    Fetches alert data from DB, looks up tenant email recipients,
    then delegates to email_service.send_alert_email.
    On final failure: logs error. Never raises.
    """
    try:
        _run_email_dispatch(alert_id, tenant_id)
    except Exception as exc:
        log.error(
            "task.dispatch_email_alert.failed",
            alert_id=alert_id,
            tenant_id=tenant_id,
            error=str(exc),
            retries=self.request.retries,
        )
        try:
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        except self.MaxRetriesExceededError:
            log.error(
                "task.dispatch_email_alert.max_retries_exceeded",
                alert_id=alert_id,
                tenant_id=tenant_id,
            )


def _run_email_dispatch(alert_id: str, tenant_id: str) -> None:
    """Load alert data and dispatch email synchronously."""
    from backend.app.services.email_service import send_alert_email

    alert_data, supplier_name, recipients = _load_alert_for_dispatch(alert_id, tenant_id)
    if alert_data is None:
        log.warning("task.dispatch_email_alert.alert_not_found", alert_id=alert_id)
        return

    send_alert_email(alert_data, supplier_name, recipients)
    log.info(
        "task.dispatch_email_alert.done",
        alert_id=alert_id,
        recipients=recipients,
    )


# ---------------------------------------------------------------------------
# Slack dispatch
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="backend.app.worker.tasks.dispatch_slack_alert",
    queue="notifications",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def dispatch_slack_alert(self: Any, alert_id: str, tenant_id: str) -> None:
    """POST alert to Slack webhook.

    Only runs if tenant has slack.enabled = True in alert_rules.channels.
    Retries 3 times. On failure: logs and moves on.
    """
    try:
        _run_slack_dispatch(alert_id, tenant_id)
    except Exception as exc:
        log.error(
            "task.dispatch_slack_alert.failed",
            alert_id=alert_id,
            tenant_id=tenant_id,
            error=str(exc),
            retries=self.request.retries,
        )
        try:
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        except self.MaxRetriesExceededError:
            log.error(
                "task.dispatch_slack_alert.max_retries_exceeded",
                alert_id=alert_id,
                tenant_id=tenant_id,
            )


def _run_slack_dispatch(alert_id: str, tenant_id: str) -> None:
    """Load alert data, check Slack enabled, and dispatch."""
    from backend.app.services.slack_service import send_slack_alert

    alert_data, supplier_name, _ = _load_alert_for_dispatch(alert_id, tenant_id)
    if alert_data is None:
        log.warning("task.dispatch_slack_alert.alert_not_found", alert_id=alert_id)
        return

    webhook_url = _get_slack_webhook(tenant_id)
    if webhook_url is None:
        log.info(
            "task.dispatch_slack_alert.slack_not_enabled",
            alert_id=alert_id,
            tenant_id=tenant_id,
        )
        return

    send_slack_alert(alert_data, supplier_name, webhook_url)
    log.info("task.dispatch_slack_alert.done", alert_id=alert_id)


# ---------------------------------------------------------------------------
# WebSocket event push
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="backend.app.worker.tasks.push_websocket_event",
    queue="websocket",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def push_websocket_event(
    self: Any,
    event_type: str,
    payload: dict[str, Any],
    tenant_id: str,
) -> None:
    """Publish event to Redis pub/sub channel for WebSocket broadcast.

    Channel: ws:{tenant_id}
    WebSocket server subscribes to this channel and pushes to connected clients.
    """
    try:
        _publish_to_redis(event_type, payload, tenant_id)
    except Exception as exc:
        log.error(
            "task.push_websocket_event.failed",
            event_type=event_type,
            tenant_id=tenant_id,
            error=str(exc),
            retries=self.request.retries,
        )
        try:
            raise self.retry(exc=exc, countdown=30)
        except self.MaxRetriesExceededError:
            log.error(
                "task.push_websocket_event.max_retries_exceeded",
                event_type=event_type,
                tenant_id=tenant_id,
            )


def _publish_to_redis(
    event_type: str, payload: dict[str, Any], tenant_id: str
) -> None:
    """Publish serialised event to the tenant's WebSocket channel."""
    import redis

    from backend.app.config import get_settings

    cfg = get_settings()
    channel = f"ws:{tenant_id}"
    message = json.dumps({"type": event_type, "payload": payload})

    client = redis.from_url(cfg.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        client.publish(channel, message)
        log.info(
            "task.push_websocket_event.published",
            channel=channel,
            event_type=event_type,
        )
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Helpers — load alert + settings from DB
# ---------------------------------------------------------------------------


def _load_alert_for_dispatch(
    alert_id: str,
    tenant_id: str,
) -> tuple[Any, str, list[str]]:
    """Return (AlertRecord | None, supplier_name, email_recipients).

    Uses asyncio.run() because Celery workers run in a sync context.
    """
    return asyncio.run(_async_load_alert(alert_id, tenant_id))


async def _async_load_alert(  # pragma: no cover
    alert_id: str,
    tenant_id: str,
) -> tuple[Any, str, list[str]]:
    import json as _json

    import asyncpg

    from backend.app.config import get_settings
    from backend.app.repositories.alert_repository import AlertRecord

    cfg = get_settings()
    dsn = (
        f"postgresql://{cfg.postgres_user}:{cfg.postgres_password}"
        f"@{cfg.postgres_host}:{cfg.postgres_port}/{cfg.postgres_db}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        raw_id = alert_id.removeprefix("alr_")
        row = await conn.fetchrow(
            """
            SELECT a.alert_type, a.severity, a.title, a.message,
                   a.metadata, a.supplier_id, s.canonical_name,
                   ar.channels
            FROM alerts a
            JOIN suppliers s ON s.id = a.supplier_id
            LEFT JOIN alert_rules ar ON ar.tenant_id = a.tenant_id
            WHERE a.id = $1::uuid AND a.tenant_id = $2::uuid
            """,
            raw_id,
            tenant_id,
        )
    finally:
        await conn.close()

    if row is None:
        return None, "", []

    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = _json.loads(metadata)

    alert = AlertRecord(
        supplier_id=row["supplier_id"],
        tenant_id=tenant_id,
        alert_type=row["alert_type"],
        severity=row["severity"],
        title=row["title"],
        message=row["message"],
        metadata=metadata or {},
    )
    supplier_name: str = row["canonical_name"]

    channels_raw = row["channels"]
    if isinstance(channels_raw, str):
        channels_raw = _json.loads(channels_raw)
    channels = channels_raw or {}
    email_cfg = channels.get("email", {})
    recipients: list[str] = email_cfg.get("recipients", []) if email_cfg.get("enabled") else []

    return alert, supplier_name, recipients


def _get_slack_webhook(tenant_id: str) -> str | None:
    """Return the Slack webhook URL if enabled for this tenant, else None."""
    return asyncio.run(_async_get_slack_webhook(tenant_id))


async def _async_get_slack_webhook(tenant_id: str) -> str | None:  # pragma: no cover
    import json as _json

    import asyncpg

    from backend.app.config import get_settings

    cfg = get_settings()
    dsn = (
        f"postgresql://{cfg.postgres_user}:{cfg.postgres_password}"
        f"@{cfg.postgres_host}:{cfg.postgres_port}/{cfg.postgres_db}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT channels FROM alert_rules WHERE tenant_id = $1::uuid",
            tenant_id,
        )
    finally:
        await conn.close()

    if row is None:
        return None

    channels_raw = row["channels"]
    if isinstance(channels_raw, str):
        channels_raw = _json.loads(channels_raw)
    channels = channels_raw or {}
    slack = channels.get("slack", {})
    if slack.get("enabled") and slack.get("webhook_url"):
        return str(slack["webhook_url"])
    return None
