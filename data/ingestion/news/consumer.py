"""Kafka consumer that enriches raw news articles via NLP and writes to Postgres.

Flow:
    raw.news topic → NewsEnrichmentConsumer → NLPProcessor → NewsRepository
                                           ↘ (on error)  → raw.dlq.news

The consumer loop never raises — failed messages are routed to the DLQ and
processing continues. This ensures a single bad article cannot stall the pipeline.

Writes go through the NewsRepository Protocol so the Postgres implementation
can be swapped for InMemoryNewsRepository in tests without touching this code.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from data.ingestion.news.models import EnrichedArticle, NewsRawEvent, RawArticle
from data.ingestion.news.nlp_processor import NLPProcessor

log = structlog.get_logger()

_TOPIC_RAW_NEWS = "raw.news"
_TOPIC_DLQ_NEWS = "raw.dlq.news"
_CONSUMER_GROUP = "news-enrichment"


# ── Repository Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class NewsRepository(Protocol):
    """Interface for persisting enriched news articles.

    Swappable: use PostgresNewsRepository in production, InMemoryNewsRepository
    in tests. Never instantiate a concrete repository directly in business logic —
    always depend on this Protocol.
    """

    async def upsert_enriched_article(self, article: EnrichedArticle) -> None:
        """Insert or update an enriched article by article_id."""
        ...

    async def article_exists(self, article_id: str) -> bool:
        """Return True if an article with this article_id is already stored."""
        ...


# ── InMemory implementation (tests) ───────────────────────────────────────────


class InMemoryNewsRepository:
    """In-memory NewsRepository for use in tests. No database required.

    Stores articles in a dict keyed by article_id.
    upsert_enriched_article overwrites the existing record on duplicate article_id.
    """

    def __init__(self) -> None:
        self._store: dict[str, EnrichedArticle] = {}

    async def upsert_enriched_article(self, article: EnrichedArticle) -> None:
        """Upsert article by article_id — overwrites on duplicate."""
        self._store[article.article_id] = article
        log.debug("memory_repo.upserted", article_id=article.article_id)

    async def article_exists(self, article_id: str) -> bool:
        """Return True if article_id is already in the store."""
        return article_id in self._store

    def all_articles(self) -> list[EnrichedArticle]:
        """Return all stored articles. Convenience method for test assertions."""
        return list(self._store.values())


# ── Postgres implementation (production) ──────────────────────────────────────


class PostgresNewsRepository:
    """Production NewsRepository backed by Postgres staging schema.

    Writes to staging.stg_news_sentiment via asyncpg.
    Requires an asyncpg connection pool — inject via constructor.

    The upsert uses ON CONFLICT (article_id) DO UPDATE so re-processing an
    article (e.g. after a bug fix) overwrites the stale record cleanly.

    Args:
        pool: An asyncpg connection pool. Caller is responsible for lifecycle.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def upsert_enriched_article(self, article: EnrichedArticle) -> None:
        """Upsert enriched article into staging.stg_news_sentiment."""
        query = """
            INSERT INTO staging.stg_news_sentiment (
                article_id, supplier_id, supplier_name_raw, title, url,
                published_at, source_name, sentiment_score, sentiment_label,
                topic_layoff, topic_bankruptcy, topic_strike, topic_disaster,
                topic_regulatory, source_credibility, word_count, processed_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12, $13,
                $14, $15, $16, $17
            )
            ON CONFLICT (article_id) DO UPDATE SET
                supplier_id       = EXCLUDED.supplier_id,
                supplier_name_raw = EXCLUDED.supplier_name_raw,
                sentiment_score   = EXCLUDED.sentiment_score,
                sentiment_label   = EXCLUDED.sentiment_label,
                topic_layoff      = EXCLUDED.topic_layoff,
                topic_bankruptcy  = EXCLUDED.topic_bankruptcy,
                topic_strike      = EXCLUDED.topic_strike,
                topic_disaster    = EXCLUDED.topic_disaster,
                topic_regulatory  = EXCLUDED.topic_regulatory,
                word_count        = EXCLUDED.word_count,
                processed_at      = EXCLUDED.processed_at
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                query,
                article.article_id,
                article.supplier_id,
                article.supplier_name_raw,
                article.title,
                article.url,
                article.published_at,
                article.source_name,
                article.sentiment_score,
                article.sentiment_label,
                article.topic_layoff,
                article.topic_bankruptcy,
                article.topic_strike,
                article.topic_disaster,
                article.topic_regulatory,
                article.source_credibility,
                article.word_count,
                article.processed_at,
            )
        log.info("postgres_repo.upserted", article_id=article.article_id)

    async def article_exists(self, article_id: str) -> bool:
        """Return True if article_id already exists in staging.stg_news_sentiment."""
        query = "SELECT 1 FROM staging.stg_news_sentiment WHERE article_id = $1 LIMIT 1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, article_id)
        return row is not None


# ── Consumer ──────────────────────────────────────────────────────────────────


class NewsEnrichmentConsumer:
    """Consumes raw.news, enriches via NLP, writes to Postgres via repository.

    Reads from:  raw.news Kafka topic
    Writes to:   staging.stg_news_sentiment (via NewsRepository)
    On error:    routes raw message to raw.dlq.news — never raises and stops

    The consumer loop runs until cancelled (e.g. asyncio.CancelledError).
    Each message is processed independently — one failure does not affect others.

    Args:
        repository: NewsRepository implementation. Inject InMemoryNewsRepository
                    in tests, PostgresNewsRepository in production.
        nlp_processor: NLPProcessor instance. Defaults to a new instance with
                       FinBERT enabled.
        settings: Application settings. Defaults to get_settings() singleton.
    """

    def __init__(
        self,
        repository: NewsRepository,
        nlp_processor: NLPProcessor | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._repository = repository
        self._nlp = nlp_processor or NLPProcessor(use_finbert=True)
        self._settings = settings or get_settings()
        self._consumer: AIOKafkaConsumer | None = None
        self._dlq_producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Start the Kafka consumer and DLQ producer."""
        self._consumer = AIOKafkaConsumer(
            _TOPIC_RAW_NEWS,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            security_protocol=self._settings.kafka_security_protocol,
            group_id=_CONSUMER_GROUP,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=True,
        )
        self._dlq_producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            security_protocol=self._settings.kafka_security_protocol,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await self._consumer.start()
        await self._dlq_producer.start()
        log.info(
            "news_consumer.started",
            topic=_TOPIC_RAW_NEWS,
            group=_CONSUMER_GROUP,
        )

    async def stop(self) -> None:
        """Stop the consumer and DLQ producer cleanly."""
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        if self._dlq_producer is not None:
            await self._dlq_producer.stop()
            self._dlq_producer = None
        log.info("news_consumer.stopped")

    async def __aenter__(self) -> "NewsEnrichmentConsumer":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def run(self) -> None:
        """Main consumer loop. Runs indefinitely until cancelled.

        Each message is processed independently. Failures are routed to the
        DLQ — the loop never raises and never stops on a single bad message.
        Exits cleanly on asyncio.CancelledError.
        """
        if self._consumer is None:
            raise RuntimeError("Consumer not started — call start() or use as context manager")

        log.info("news_consumer.running", topic=_TOPIC_RAW_NEWS)
        async for kafka_message in self._consumer:
            try:
                await self.process_message(kafka_message.value)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "news_consumer.unhandled_error",
                    error=str(exc),
                    offset=kafka_message.offset,
                )
                await self._send_to_dlq(
                    original_payload=kafka_message.value,
                    error=str(exc),
                )

    async def process_message(self, raw_value: dict[str, Any]) -> None:
        """Validate, enrich, and persist a single raw.news message.

        Validates the payload against NewsRawEvent. On validation failure,
        routes to DLQ immediately without attempting NLP. On NLP or persistence
        failure, routes to DLQ after logging.

        Args:
            raw_value: Deserialised Kafka message value (dict).
        """
        try:
            event = NewsRawEvent.model_validate(raw_value)
        except ValidationError as exc:
            log.warning(
                "news_consumer.invalid_message",
                error=str(exc),
                payload=raw_value,
            )
            await self._send_to_dlq(original_payload=raw_value, error=str(exc))
            return

        raw_article = _event_to_raw_article(event)

        try:
            enriched = await self._nlp.process_article(raw_article)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "news_consumer.nlp_failed",
                article_id=raw_article.article_id,
                error=str(exc),
            )
            await self._send_to_dlq(original_payload=raw_value, error=f"NLP failed: {exc}")
            return

        try:
            await self._repository.upsert_enriched_article(enriched)
            log.info(
                "news_consumer.article_enriched",
                article_id=enriched.article_id,
                sentiment=enriched.sentiment_label,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "news_consumer.write_failed",
                article_id=enriched.article_id,
                error=str(exc),
            )
            await self._send_to_dlq(original_payload=raw_value, error=f"Write failed: {exc}")

    async def _send_to_dlq(
        self, original_payload: dict[str, Any], error: str
    ) -> None:
        """Route a failed message to raw.dlq.news.

        Never raises — if the DLQ send itself fails, the error is logged and
        swallowed so the consumer loop can continue.
        """
        dlq_payload: dict[str, Any] = {
            "original_payload": original_payload,
            "error": error,
            "failed_at": datetime.now(tz=timezone.utc).isoformat(),
            "source_topic": _TOPIC_RAW_NEWS,
        }
        log.warning("news_consumer.dlq", error=error, topic=_TOPIC_DLQ_NEWS)
        try:
            if self._dlq_producer is not None:
                await self._dlq_producer.send_and_wait(_TOPIC_DLQ_NEWS, value=dlq_payload)
        except Exception as exc:  # noqa: BLE001
            log.error("news_consumer.dlq_send_failed", error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _event_to_raw_article(event: NewsRawEvent) -> RawArticle:
    """Convert a NewsRawEvent (Kafka message) to a RawArticle for NLP processing.

    source_credibility defaults to 0.50 — the scraper assigns the real value
    before publishing, but we re-derive it here as a safety net in case the
    field was not included in older message versions.
    """
    from data.ingestion.news.scraper import _credibility_for_url

    return RawArticle(
        article_id=event.article_id,
        url=event.url,
        title=event.title,
        content=event.content,
        published_at=event.published_at,
        source_name=event.source_name,
        source_credibility=_credibility_for_url(event.url),
        ingested_at=event.ingested_at,
        ingestion_source=event.source,
    )
