"""Tests for ScoresConsumer — message processing and fanout."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.consumers.scores_consumer import ScoresConsumer, ScoreUpdatedEvent

_SUPPLIER_ID = "sup_abc123"
_TENANT_ID = "10000000-0000-0000-0000-000000000001"

_VALID_EVENT = {
    "supplier_id": _SUPPLIER_ID,
    "score": 72,
    "risk_level": "high",
    "model_version": "heuristic_v0",
    "scored_at": "2026-03-07T10:00:00+00:00",
    "feature_date": "2026-03-07",
    "signal_breakdown": {
        "risk_level": "high",
        "financial_score": 30.0,
        "news_score": 25.0,
        "shipping_score": 10.0,
        "geo_score": 5.0,
        "macro_score": 2.0,
        "top_drivers": [],
        "all_signals": [],
        "data_completeness": 0.9,
    },
}


def _make_consumer() -> ScoresConsumer:
    consumer = ScoresConsumer.__new__(ScoresConsumer)
    consumer._cfg = MagicMock()
    consumer._cfg.kafka_bootstrap_servers = "localhost:9092"
    consumer._pool = MagicMock()
    consumer._consumer = None
    consumer._running = False
    return consumer


class TestHandleMessage:
    async def test_valid_message_calls_process_event(self) -> None:
        consumer = _make_consumer()
        consumer._process_score_event = AsyncMock()
        consumer._send_to_dlq = AsyncMock()

        await consumer._handle_message(json.dumps(_VALID_EVENT))

        consumer._process_score_event.assert_awaited_once()
        consumer._send_to_dlq.assert_not_awaited()

    async def test_invalid_json_routes_to_dlq(self) -> None:
        consumer = _make_consumer()
        consumer._process_score_event = AsyncMock()
        consumer._send_to_dlq = AsyncMock()

        await consumer._handle_message("not-valid-json{{{")

        consumer._send_to_dlq.assert_awaited_once()
        consumer._process_score_event.assert_not_awaited()

    async def test_missing_required_field_routes_to_dlq(self) -> None:
        consumer = _make_consumer()
        consumer._process_score_event = AsyncMock()
        consumer._send_to_dlq = AsyncMock()

        incomplete = {"supplier_id": "sup_abc", "score": 50}  # missing required fields
        await consumer._handle_message(json.dumps(incomplete))

        consumer._send_to_dlq.assert_awaited_once()
        consumer._process_score_event.assert_not_awaited()


class TestProcessScoreEvent:
    async def test_happy_path_evaluates_and_fires_tasks(self) -> None:
        """Valid event → alert engine called, Celery tasks fired."""
        consumer = _make_consumer()
        consumer._load_previous_score = AsyncMock(return_value=None)

        event = ScoreUpdatedEvent(**_VALID_EVENT)

        mock_alert_ids = ["alr_001"]
        mock_engine = AsyncMock()
        mock_engine.evaluate = AsyncMock(return_value=mock_alert_ids)

        mock_email_task = MagicMock()
        mock_slack_task = MagicMock()
        mock_ws_task = MagicMock()

        with patch(
            "backend.app.consumers.scores_consumer.get_tenants_monitoring_supplier",
            AsyncMock(return_value=[(_TENANT_ID, MagicMock())]),
        ), patch(
            "backend.app.consumers.scores_consumer.AlertEngine",
            return_value=mock_engine,
        ), patch(
            "backend.app.consumers.scores_consumer.PostgresAlertRepository",
            return_value=MagicMock(),
        ), patch(
            "backend.app.worker.tasks.dispatch_email_alert",
            mock_email_task,
        ), patch(
            "backend.app.worker.tasks.dispatch_slack_alert",
            mock_slack_task,
        ), patch(
            "backend.app.worker.tasks.push_websocket_event",
            mock_ws_task,
        ):
            await consumer._process_score_event(event)

        mock_email_task.delay.assert_called_once_with("alr_001", _TENANT_ID)
        mock_slack_task.delay.assert_called_once_with("alr_001", _TENANT_ID)
        mock_ws_task.delay.assert_called_once()

    async def test_one_tenant_failure_does_not_stop_other_tenants(self) -> None:
        """If evaluation raises for tenant 1, tenant 2 still gets processed."""
        consumer = _make_consumer()
        consumer._load_previous_score = AsyncMock(return_value=None)

        event = ScoreUpdatedEvent(**_VALID_EVENT)

        tenant1 = "10000000-0000-0000-0000-000000000001"
        tenant2 = "20000000-0000-0000-0000-000000000002"

        call_count = 0

        async def _mock_evaluate(**kwargs: object) -> list[str]:
            nonlocal call_count
            call_count += 1
            if kwargs.get("tenant_id") == tenant1:
                raise RuntimeError("DB error for tenant 1")
            return []

        mock_engine = MagicMock()
        mock_engine.evaluate = _mock_evaluate
        mock_ws_task = MagicMock()

        with patch(
            "backend.app.consumers.scores_consumer.get_tenants_monitoring_supplier",
            AsyncMock(return_value=[(tenant1, MagicMock()), (tenant2, MagicMock())]),
        ), patch(
            "backend.app.consumers.scores_consumer.AlertEngine",
            return_value=mock_engine,
        ), patch(
            "backend.app.consumers.scores_consumer.PostgresAlertRepository",
            return_value=MagicMock(),
        ), patch(
            "backend.app.worker.tasks.push_websocket_event",
            mock_ws_task,
        ):
            # Should not raise — errors are caught per tenant
            await consumer._process_score_event(event)

        # Both tenants were attempted via evaluate (tenant1 raised, tenant2 returned [])
        assert call_count == 2
        # WebSocket event fired only for tenant2 — tenant1's exception aborted its try block
        assert mock_ws_task.delay.call_count == 1
