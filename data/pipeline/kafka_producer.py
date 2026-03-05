"""Kafka producer for the Supplier Risk Intelligence Platform.

Validates all events via Pydantic before publishing.
Routes invalid records to dead-letter queue (raw.dlq.*) — never raises on bad data.
DLQ messages include original payload, error message, source topic, and timestamp.
All operations logged with structlog.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from aiokafka import AIOKafkaProducer

from backend.app.config import Settings, get_settings
from data.ingestion.sec_edgar.models import SECRawEvent

log = structlog.get_logger()


class SupplierRiskKafkaProducer:
    """Publishes validated supply chain risk events to Kafka.

    Features:
    - Pydantic validation before every publish — invalid events go to DLQ
    - Dead-letter queue routing: raw.dlq.{source} for failed records
    - Structured logging on every publish and every DLQ send
    - Async context manager for clean lifecycle management

    DLQ message schema:
        {"original_payload": {...}, "error": "...", "failed_at": "ISO-8601", "source_topic": "..."}
    """

    TOPIC_MAP: dict[str, str] = {
        "sec": "raw.sec",
        "news": "raw.news",
        "ais": "raw.ais",
        "macro": "raw.macro",
        "geo": "raw.geo",
    }
    DLQ_TOPIC_PREFIX = "raw.dlq"

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialise with application settings."""
        self._settings = settings or get_settings()
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Start the underlying AIOKafkaProducer and connect to the broker."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            security_protocol=self._settings.kafka_security_protocol,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self._producer.start()
        log.info(
            "kafka.producer_started",
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
        )

    async def stop(self) -> None:
        """Flush pending messages and stop the producer."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            log.info("kafka.producer_stopped")

    async def __aenter__(self) -> "SupplierRiskKafkaProducer":
        """Start the producer on context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Stop the producer on context manager exit."""
        await self.stop()

    async def publish_sec_event(self, event: SECRawEvent) -> bool:
        """Publish a validated SEC filing event to the raw.sec topic.

        Validates the event against the SECRawEvent schema before publishing.
        Routes to DLQ if validation or serialisation fails.

        Args:
            event: The SECRawEvent to publish.

        Returns:
            True if published successfully, False if routed to DLQ.
        """
        topic = self.TOPIC_MAP["sec"]
        try:
            payload = json.loads(event.model_dump_json())
            return await self._publish(topic, payload, key=event.cik)
        except Exception as exc:
            log.error(
                "kafka.publish_failed",
                topic=topic,
                cik=getattr(event, "cik", "unknown"),
                error=str(exc),
            )
            raw: dict[str, Any] = {}
            try:
                raw = event.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                pass
            await self._publish_to_dlq(topic, raw, str(exc))
            return False

    async def _publish(
        self, topic: str, payload: dict[str, Any], key: str | None = None
    ) -> bool:
        """Serialise and send a message to a Kafka topic.

        Args:
            topic: Target Kafka topic name.
            payload: JSON-serialisable dict.
            key: Optional message key (used for partition routing).

        Returns:
            True on success.

        Raises:
            Exception: Propagates producer errors — caller handles DLQ routing.
        """
        if self._producer is None:
            raise RuntimeError("Producer not started — call start() or use as context manager")
        await self._producer.send_and_wait(topic, value=payload, key=key)
        log.info("kafka.published", topic=topic, key=key)
        return True

    async def _publish_to_dlq(
        self, source_topic: str, original_payload: dict[str, Any], error: str
    ) -> None:
        """Send a failed message to the dead-letter queue topic.

        DLQ topic is derived from source_topic: raw.dlq.{last_segment}.
        Always attempts to send even if the producer is unhealthy.

        Args:
            source_topic: The topic the message was intended for.
            original_payload: The payload that failed to publish.
            error: Human-readable error description.
        """
        dlq_topic = f"{self.DLQ_TOPIC_PREFIX}.{source_topic.split('.')[-1]}"
        dlq_payload: dict[str, Any] = {
            "original_payload": original_payload,
            "error": error,
            "failed_at": datetime.now(tz=timezone.utc).isoformat(),
            "source_topic": source_topic,
        }
        log.warning(
            "kafka.dlq",
            dlq_topic=dlq_topic,
            source_topic=source_topic,
            error=error,
        )
        try:
            if self._producer is not None:
                await self._producer.send_and_wait(dlq_topic, value=dlq_payload)
        except Exception as exc:  # noqa: BLE001
            log.error("kafka.dlq_failed", dlq_topic=dlq_topic, error=str(exc))
