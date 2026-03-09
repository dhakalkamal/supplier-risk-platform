"""
Base Kafka consumer.

Handles the generic consume → validate → write/DLQ loop.
Source-specific consumers subclass this and implement `_process`.

Architecture rules followed:
  - On valid:   write to Postgres pipeline schema
  - On invalid: publish to raw.dlq.{source} + structured log
  - Never let one bad record stop the pipeline (catch all, continue)
  - Retry up to 3x with backoff on transient DB/Kafka errors
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import BaseModel, ValidationError

from pipeline.schemas.raw_events import DeadLetterEvent, TOPIC_SCHEMA_MAP
from pipeline.db.repository import PipelineRepository

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0   # seconds; doubles each attempt


class BaseConsumer(ABC):
    """
    Generic Kafka consumer loop.

    Subclasses implement `_persist(event, repo)` to write a validated
    event to the correct pipeline.raw_* table.
    """

    topic: str         # e.g. "raw.sec"
    source: str        # e.g. "sec"  — used for DLQ topic and logging
    group_id: str      # consumer group

    def __init__(
        self,
        bootstrap_servers: str,
        repo: PipelineRepository,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._repo = repo
        self._schema = TOPIC_SCHEMA_MAP[self.topic]
        self._dlq_topic = f"raw.dlq.{self.source}"

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start consuming. Runs until cancelled."""
        consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self._bootstrap,
            group_id=self.group_id,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        )

        await consumer.start()
        await producer.start()
        logger.info("Consumer started", extra={"topic": self.topic, "source": self.source})

        try:
            async for msg in consumer:
                await self._handle_message(msg.value, producer)
                await consumer.commit()
        except asyncio.CancelledError:
            logger.info("Consumer shutting down", extra={"topic": self.topic})
        finally:
            await consumer.stop()
            await producer.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # Message handling
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_message(
        self,
        raw_payload: dict[str, Any],
        producer: AIOKafkaProducer,
    ) -> None:
        """Validate → persist, or route to DLQ on failure."""

        # 1. Pydantic validation
        try:
            event = self._schema.model_validate(raw_payload)
        except ValidationError as exc:
            logger.warning(
                "Validation failed — routing to DLQ",
                extra={
                    "source": self.source,
                    "topic": self.topic,
                    "error_type": type(exc).__name__,
                    "errors": exc.errors(),
                },
            )
            await self._send_to_dlq(
                producer=producer,
                payload=raw_payload,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return

        # 2. Persist with retry
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await self._persist(event, self._repo)
                logger.debug(
                    "Record written",
                    extra={"source": self.source, "attempt": attempt},
                )
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == _MAX_RETRIES:
                    logger.error(
                        "Persist failed after retries — routing to DLQ",
                        extra={
                            "source": self.source,
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        },
                        exc_info=True,
                    )
                    await self._send_to_dlq(
                        producer=producer,
                        payload=raw_payload,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        retry_count=attempt,
                    )
                else:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Persist failed, retrying",
                        extra={
                            "source": self.source,
                            "attempt": attempt,
                            "retry_in_seconds": delay,
                        },
                    )
                    await asyncio.sleep(delay)

    async def _send_to_dlq(
        self,
        producer: AIOKafkaProducer,
        payload: dict[str, Any],
        error_type: str,
        error_message: str,
        retry_count: int = 0,
    ) -> None:
        dlq_event = DeadLetterEvent(
            original_topic=self.topic,
            original_payload=payload,
            error_type=error_type,
            error_message=error_message,
            failed_at=datetime.now(tz=timezone.utc),
            retry_count=retry_count,
            source=self.source,
        )
        await producer.send(
            self._dlq_topic,
            value=dlq_event.model_dump(),
        )
        logger.info(
            "DLQ message sent",
            extra={"dlq_topic": self._dlq_topic, "source": self.source},
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Abstract — subclasses implement the actual DB write
    # ─────────────────────────────────────────────────────────────────────────

    @abstractmethod
    async def _persist(self, event: BaseModel, repo: PipelineRepository) -> None:
        """Write a validated event to the appropriate pipeline.raw_* table."""
        ...
