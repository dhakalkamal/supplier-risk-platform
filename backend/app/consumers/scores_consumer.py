"""Kafka consumer — listens to scores.updated topic and fans out alert evaluation.

For each score event:
1. Load previous score from DB
2. Get all tenants monitoring this supplier
3. For each tenant: evaluate alert rules, create alerts, fire Celery tasks
4. Publish scores.updated WebSocket event to Redis pub/sub

One failure for one tenant must not stop processing for other tenants.
Run with: python -m backend.app.consumers.scores_consumer
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import date, datetime, timezone
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
from pydantic import BaseModel, ValidationError

from backend.app.config import get_settings
from backend.app.db.connection import create_pool
from backend.app.repositories.alert_repository import PostgresAlertRepository
from backend.app.services.alert_engine import AlertEngine
from backend.app.services.alert_rules_service import get_tenants_monitoring_supplier
from ml.scoring.models import RiskScoreOutput, SignalContribution

log = structlog.get_logger()

TOPIC = "scores.updated"
GROUP_ID = "alert-engine"
DLQ_TOPIC = "scores.updated.dlq"


class ScoreUpdatedEvent(BaseModel):
    """Schema for messages on the scores.updated Kafka topic."""

    supplier_id: str
    score: int
    risk_level: str
    model_version: str
    scored_at: datetime
    feature_date: date
    signal_breakdown: dict[str, Any]  # full RiskScoreOutput serialised


def _event_to_risk_score(event: ScoreUpdatedEvent) -> RiskScoreOutput:
    """Reconstruct RiskScoreOutput from the serialised signal_breakdown payload."""
    bd = event.signal_breakdown
    signals = [SignalContribution(**s) for s in bd.get("all_signals", [])]
    top = [SignalContribution(**s) for s in bd.get("top_drivers", [])]
    return RiskScoreOutput(
        supplier_id=event.supplier_id,
        score=event.score,
        risk_level=bd.get("risk_level", event.risk_level),
        financial_score=bd.get("financial_score", 0.0),
        news_score=bd.get("news_score", 0.0),
        shipping_score=bd.get("shipping_score", 0.0),
        geo_score=bd.get("geo_score", 0.0),
        macro_score=bd.get("macro_score", 0.0),
        top_drivers=top[:5],
        all_signals=signals,
        model_version=event.model_version,
        feature_date=event.feature_date,
        scored_at=event.scored_at,
        data_completeness=bd.get("data_completeness", 0.0),
    )


class ScoresConsumer:
    """Consumes scores.updated events and fans out to alert engine."""

    def __init__(self) -> None:
        self._cfg = get_settings()
        self._pool: Any = None
        self._consumer: AIOKafkaConsumer | None = None
        self._running = False

    async def run(self) -> None:
        """Main consumer loop. Never raises — logs errors and continues."""
        self._pool = await create_pool(self._cfg)
        self._consumer = AIOKafkaConsumer(
            TOPIC,
            bootstrap_servers=self._cfg.kafka_bootstrap_servers,
            group_id=GROUP_ID,
            value_deserializer=lambda b: b.decode("utf-8"),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await self._consumer.start()
        self._running = True
        log.info("scores_consumer.started", topic=TOPIC, group=GROUP_ID)

        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                await self._handle_message(msg.value)
        except KafkaError as exc:
            log.error("scores_consumer.kafka_error", error=str(exc))
        finally:
            await self._consumer.stop()
            log.info("scores_consumer.stopped")

    def stop(self) -> None:
        self._running = False

    async def _handle_message(self, raw: str) -> None:
        """Parse message and call _process_score_event. Route bad messages to DLQ."""
        try:
            data = json.loads(raw)
            event = ScoreUpdatedEvent(**data)
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            log.error("scores_consumer.invalid_message", error=str(exc), raw=raw[:200])
            await self._send_to_dlq(raw, reason=str(exc))
            return

        await self._process_score_event(event)

    async def _process_score_event(self, event: ScoreUpdatedEvent) -> None:
        """Fan out alert evaluation to all tenants monitoring this supplier."""
        from backend.app.worker.tasks import (
            dispatch_email_alert,
            dispatch_slack_alert,
            push_websocket_event,
        )

        new_score = _event_to_risk_score(event)
        previous_score = await self._load_previous_score(event.supplier_id)
        tenants = await get_tenants_monitoring_supplier(event.supplier_id, self._pool)

        for tenant_id, rules in tenants:
            try:
                repo = PostgresAlertRepository(self._pool)
                engine = AlertEngine(repo)
                alert_ids = await engine.evaluate(
                    supplier_id=event.supplier_id,
                    tenant_id=tenant_id,
                    new_score=new_score,
                    previous_score=previous_score,
                    rules=rules,
                )
                for alert_id in alert_ids:
                    dispatch_email_alert.delay(alert_id, tenant_id)
                    dispatch_slack_alert.delay(alert_id, tenant_id)

                push_websocket_event.delay(
                    event_type="score.updated",
                    payload={
                        "supplier_id": event.supplier_id,
                        "score": event.score,
                        "risk_level": event.risk_level,
                        "scored_at": event.scored_at.isoformat(),
                    },
                    tenant_id=tenant_id,
                )
            except Exception as exc:
                log.error(
                    "scores_consumer.tenant_processing_failed",
                    tenant_id=tenant_id,
                    supplier_id=event.supplier_id,
                    error=str(exc),
                )
                # Continue to next tenant — one failure must not stop others

        log.info(
            "scores_consumer.event_processed",
            supplier_id=event.supplier_id,
            score=event.score,
            tenants_count=len(tenants),
        )

    async def _load_previous_score(self, supplier_id: str) -> RiskScoreOutput | None:
        """Load the most recent score for this supplier from DB (before today's upsert)."""
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT signal_breakdown, score, risk_level, model_version,
                           feature_date, scored_at
                    FROM scores.supplier_daily_scores
                    WHERE supplier_id = $1
                    ORDER BY feature_date DESC
                    LIMIT 1
                    """,
                    supplier_id,
                )
            if row is None:
                return None
            bd = row["signal_breakdown"]
            if isinstance(bd, str):
                bd = json.loads(bd)
            signals = [SignalContribution(**s) for s in bd.get("all_signals", [])]
            top = [SignalContribution(**s) for s in bd.get("top_drivers", [])]
            return RiskScoreOutput(
                supplier_id=supplier_id,
                score=row["score"],
                risk_level=row["risk_level"],
                financial_score=bd.get("financial_score", 0.0),
                news_score=bd.get("news_score", 0.0),
                shipping_score=bd.get("shipping_score", 0.0),
                geo_score=bd.get("geo_score", 0.0),
                macro_score=bd.get("macro_score", 0.0),
                top_drivers=top[:5],
                all_signals=signals,
                model_version=row["model_version"],
                feature_date=row["feature_date"],
                scored_at=row["scored_at"],
                data_completeness=bd.get("data_completeness", 0.0),
            )
        except Exception as exc:
            log.warning(
                "scores_consumer.previous_score_load_failed",
                supplier_id=supplier_id,
                error=str(exc),
            )
            return None

    async def _send_to_dlq(self, raw: str, reason: str) -> None:
        """Publish unparseable messages to the DLQ topic."""
        from aiokafka import AIOKafkaProducer

        try:
            producer = AIOKafkaProducer(
                bootstrap_servers=self._cfg.kafka_bootstrap_servers
            )
            await producer.start()
            try:
                payload = json.dumps(
                    {
                        "original": raw,
                        "reason": reason,
                        "ts": datetime.now(tz=timezone.utc).isoformat(),
                    }
                ).encode()
                await producer.send_and_wait(DLQ_TOPIC, payload)
            finally:
                await producer.stop()
        except Exception as exc:
            log.error("scores_consumer.dlq_send_failed", error=str(exc))


async def _main() -> None:
    consumer = ScoresConsumer()

    loop = asyncio.get_event_loop()

    def _shutdown(sig: signal.Signals) -> None:
        log.info("scores_consumer.shutdown_signal", signal=sig.name)
        consumer.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    await consumer.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
