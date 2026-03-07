"""Tests for Celery alert dispatch tasks.

Tasks are tested via their private helper functions (_run_email_dispatch,
_run_slack_dispatch, _publish_to_redis) to avoid starting a real Celery worker.
The actual .delay() / .apply() mechanics are tested by verifying the task
is correctly registered on the Celery app.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backend.app.repositories.alert_repository import AlertRecord
from backend.app.worker.celery_app import celery_app
from backend.app.worker.tasks import (
    _publish_to_redis,
    _run_email_dispatch,
    _run_slack_dispatch,
)

_ALERT_ID = "alr_00000001000000000000000000000001"
_TENANT_ID = "10000000-0000-0000-0000-000000000001"

_MOCK_ALERT = AlertRecord(
    supplier_id="sup_abc123",
    tenant_id=_TENANT_ID,
    alert_type="score_spike",
    severity="high",
    title="Risk score spiked +20 points",
    message="Score rose from 50 to 70.",
    metadata={"delta": 20},
    fired_at=datetime.now(tz=timezone.utc),
)


# ---------------------------------------------------------------------------
# Task registration
# ---------------------------------------------------------------------------


class TestTaskRegistration:
    def test_all_tasks_registered_on_celery_app(self) -> None:
        registered = celery_app.tasks
        assert "backend.app.worker.tasks.dispatch_email_alert" in registered
        assert "backend.app.worker.tasks.dispatch_slack_alert" in registered
        assert "backend.app.worker.tasks.push_websocket_event" in registered


# ---------------------------------------------------------------------------
# dispatch_email_alert
# ---------------------------------------------------------------------------


class TestDispatchEmailAlert:
    async def test_calls_email_service_with_correct_data(self) -> None:
        """_run_email_dispatch loads alert data then calls send_alert_email."""
        with patch(
            "backend.app.worker.tasks._load_alert_for_dispatch",
            return_value=(_MOCK_ALERT, "Acme Corp", ["admin@test.example"]),
        ), patch(
            "backend.app.services.email_service.send_alert_email",
        ) as mock_send:
            _run_email_dispatch(_ALERT_ID, _TENANT_ID)

        mock_send.assert_called_once_with(
            _MOCK_ALERT, "Acme Corp", ["admin@test.example"]
        )

    async def test_skips_when_alert_not_found(self) -> None:
        """If load returns None, email service is not called."""
        with patch(
            "backend.app.worker.tasks._load_alert_for_dispatch",
            return_value=(None, "", []),
        ), patch(
            "backend.app.services.email_service.send_alert_email",
        ) as mock_send:
            _run_email_dispatch(_ALERT_ID, _TENANT_ID)

        mock_send.assert_not_called()

    async def test_dispatch_email_alert_task_does_not_raise_on_failure(self) -> None:
        """dispatch_email_alert catches all exceptions — never raises to Celery."""
        from backend.app.worker.tasks import dispatch_email_alert

        celery_app.conf.task_always_eager = True
        try:
            with patch(
                "backend.app.worker.tasks._run_email_dispatch",
                side_effect=RuntimeError("SendGrid down"),
            ):
                # apply() runs the task synchronously; should not raise
                dispatch_email_alert.apply(args=(_ALERT_ID, _TENANT_ID))
                # Task raised internally but was caught — no exception escapes to the caller
        finally:
            celery_app.conf.task_always_eager = False


# ---------------------------------------------------------------------------
# dispatch_slack_alert
# ---------------------------------------------------------------------------


class TestDispatchSlackAlert:
    async def test_skips_when_slack_not_enabled(self) -> None:
        """If _get_slack_webhook returns None, slack service is not called."""
        with patch(
            "backend.app.worker.tasks._load_alert_for_dispatch",
            return_value=(_MOCK_ALERT, "Acme Corp", []),
        ), patch(
            "backend.app.worker.tasks._get_slack_webhook",
            return_value=None,
        ), patch(
            "backend.app.services.slack_service.send_slack_alert",
        ) as mock_slack:
            _run_slack_dispatch(_ALERT_ID, _TENANT_ID)

        mock_slack.assert_not_called()

    async def test_posts_to_correct_webhook_url(self) -> None:
        """When Slack is enabled, send_slack_alert is called with the webhook URL."""
        webhook = "https://hooks.slack.com/services/T000/B000/xxx"
        with patch(
            "backend.app.worker.tasks._load_alert_for_dispatch",
            return_value=(_MOCK_ALERT, "Acme Corp", []),
        ), patch(
            "backend.app.worker.tasks._get_slack_webhook",
            return_value=webhook,
        ), patch(
            "backend.app.services.slack_service.send_slack_alert",
        ) as mock_slack:
            _run_slack_dispatch(_ALERT_ID, _TENANT_ID)

        mock_slack.assert_called_once_with(_MOCK_ALERT, "Acme Corp", webhook)


# ---------------------------------------------------------------------------
# push_websocket_event
# ---------------------------------------------------------------------------


class TestPushWebSocketEvent:
    async def test_publishes_to_correct_redis_channel(self) -> None:
        """_publish_to_redis publishes to ws:{tenant_id}."""
        import redis as _redis

        mock_client = MagicMock()
        with patch.object(_redis, "from_url", return_value=mock_client):
            _publish_to_redis("alert.fired", {"alert_id": _ALERT_ID}, _TENANT_ID)

        mock_client.publish.assert_called_once()
        channel = mock_client.publish.call_args[0][0]
        assert channel == f"ws:{_TENANT_ID}"

    async def test_published_message_contains_event_type(self) -> None:
        """The published message JSON includes the event type."""
        import json

        import redis as _redis

        mock_client = MagicMock()
        with patch.object(_redis, "from_url", return_value=mock_client):
            _publish_to_redis("score.updated", {"score": 75}, _TENANT_ID)

        message_str = mock_client.publish.call_args[0][1]
        message = json.loads(message_str)
        assert message["type"] == "score.updated"
        assert message["payload"]["score"] == 75
