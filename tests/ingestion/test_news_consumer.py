"""Tests for data.ingestion.news.consumer.

No real Kafka broker or Postgres connection required.
InMemoryNewsRepository is used for all persistence assertions.
NLPProcessor runs with use_finbert=False (lexicon scorer) for speed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from data.ingestion.news.consumer import (
    InMemoryNewsRepository,
    NewsEnrichmentConsumer,
    NewsRepository,
)
from data.ingestion.news.models import EnrichedArticle
from data.ingestion.news.nlp_processor import NLPProcessor

# ── Shared fixture data ────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

_VALID_RAW_MESSAGE: dict[str, Any] = {
    "source": "newsapi",
    "article_id": "abc123def456",
    "url": "https://reuters.com/business/acme-corp-2024",
    "title": "Acme Corp announces workforce reduction",
    "content": "Acme Corp said it will cut 500 jobs.",
    "published_at": "2024-01-15T10:00:00+00:00",
    "source_name": "Reuters",
    "ingested_at": "2024-01-15T10:01:00+00:00",
}

_INVALID_RAW_MESSAGE: dict[str, Any] = {
    "source": "unknown_source",  # not in Literal["newsapi", "gdelt"]
    "article_id": "bad123",
    # missing required fields: url, title, published_at, etc.
}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> InMemoryNewsRepository:
    return InMemoryNewsRepository()


@pytest.fixture
def fast_processor() -> NLPProcessor:
    """NLPProcessor with FinBERT disabled for fast test execution."""
    return NLPProcessor(use_finbert=False)


@pytest.fixture
def consumer(repo, fast_processor) -> NewsEnrichmentConsumer:
    """Consumer with InMemory repo and lexicon NLP — no external dependencies."""
    return NewsEnrichmentConsumer(repository=repo, nlp_processor=fast_processor)


# ── InMemoryNewsRepository ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inmemory_repo_upsert_stores_article(repo):
    """upsert_enriched_article stores the article and it's retrievable."""
    article = _make_enriched_article("id-001")
    await repo.upsert_enriched_article(article)
    assert await repo.article_exists("id-001") is True


@pytest.mark.asyncio
async def test_inmemory_repo_article_exists_false_for_unknown(repo):
    """article_exists returns False for an article_id never stored."""
    assert await repo.article_exists("nonexistent-id") is False


@pytest.mark.asyncio
async def test_inmemory_repo_upsert_overwrites_duplicate(repo):
    """Upserting the same article_id twice overwrites — no duplicate stored."""
    article_v1 = _make_enriched_article("id-002", sentiment_label="neutral")
    article_v2 = _make_enriched_article("id-002", sentiment_label="negative")

    await repo.upsert_enriched_article(article_v1)
    await repo.upsert_enriched_article(article_v2)

    all_articles = repo.all_articles()
    assert len(all_articles) == 1
    assert all_articles[0].sentiment_label == "negative"


@pytest.mark.asyncio
async def test_inmemory_repo_stores_multiple_distinct_articles(repo):
    """Multiple articles with different article_ids are all stored."""
    for i in range(5):
        await repo.upsert_enriched_article(_make_enriched_article(f"id-{i:03d}"))
    assert len(repo.all_articles()) == 5


# ── NewsRepository Protocol ───────────────────────────────────────────────────


def test_inmemory_repo_satisfies_protocol():
    """InMemoryNewsRepository satisfies the NewsRepository Protocol at runtime."""
    repo = InMemoryNewsRepository()
    assert isinstance(repo, NewsRepository)


# ── process_message — happy path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_message_valid_article_stored_in_repo(consumer, repo):
    """A valid raw message is enriched and written to the repository."""
    await consumer.process_message(_VALID_RAW_MESSAGE)
    assert await repo.article_exists(_VALID_RAW_MESSAGE["article_id"]) is True


@pytest.mark.asyncio
async def test_process_message_enriched_article_has_correct_url(consumer, repo):
    """The stored EnrichedArticle preserves the URL from the raw message."""
    await consumer.process_message(_VALID_RAW_MESSAGE)
    articles = repo.all_articles()
    assert articles[0].url == _VALID_RAW_MESSAGE["url"]


@pytest.mark.asyncio
async def test_process_message_enriched_article_has_sentiment(consumer, repo):
    """The stored article has a valid sentiment_label."""
    await consumer.process_message(_VALID_RAW_MESSAGE)
    article = repo.all_articles()[0]
    assert article.sentiment_label in {"positive", "negative", "neutral"}
    assert -1.0 <= article.sentiment_score <= 1.0


@pytest.mark.asyncio
async def test_process_message_detects_layoff_topic(consumer, repo):
    """Articles mentioning 'workforce reduction' set topic_layoff = True."""
    # _VALID_RAW_MESSAGE title contains "workforce reduction"
    await consumer.process_message(_VALID_RAW_MESSAGE)
    article = repo.all_articles()[0]
    assert article.topic_layoff is True


# ── process_message — DLQ routing ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_message_invalid_message_routed_to_dlq(consumer, repo):
    """An invalid message (schema violation) triggers DLQ routing, not an exception."""
    with patch.object(consumer, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq:
        await consumer.process_message(_INVALID_RAW_MESSAGE)

    mock_dlq.assert_called_once()
    # Nothing should have been written to the repo
    assert len(repo.all_articles()) == 0


@pytest.mark.asyncio
async def test_process_message_nlp_failure_routed_to_dlq(consumer, repo):
    """If NLP processing raises, the message is routed to DLQ, not re-raised."""
    with patch.object(
        consumer._nlp, "process_article", side_effect=RuntimeError("NLP crash")
    ):
        with patch.object(consumer, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await consumer.process_message(_VALID_RAW_MESSAGE)

    mock_dlq.assert_called_once()
    call_kwargs = mock_dlq.call_args
    assert "NLP failed" in call_kwargs.kwargs.get("error", "") or \
           "NLP failed" in str(call_kwargs.args)
    assert len(repo.all_articles()) == 0


@pytest.mark.asyncio
async def test_process_message_write_failure_routed_to_dlq(consumer, repo):
    """If the repository write raises, the message is routed to DLQ."""
    with patch.object(
        repo, "upsert_enriched_article", side_effect=Exception("DB connection lost")
    ):
        with patch.object(consumer, "_send_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await consumer.process_message(_VALID_RAW_MESSAGE)

    mock_dlq.assert_called_once()


@pytest.mark.asyncio
async def test_process_message_does_not_raise_on_invalid_input(consumer):
    """process_message never raises — invalid input is silently routed to DLQ."""
    with patch.object(consumer, "_send_to_dlq", new_callable=AsyncMock):
        # Should not raise
        await consumer.process_message(_INVALID_RAW_MESSAGE)


# ── send_to_dlq ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_to_dlq_does_not_raise_when_producer_is_none(consumer):
    """_send_to_dlq is safe to call even when _dlq_producer is None (not started)."""
    assert consumer._dlq_producer is None
    # Must not raise
    await consumer._send_to_dlq(
        original_payload={"some": "payload"}, error="test error"
    )


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_processing_same_article_twice_does_not_duplicate(consumer, repo):
    """Processing the same message twice upserts, not inserts — one article stored."""
    await consumer.process_message(_VALID_RAW_MESSAGE)
    await consumer.process_message(_VALID_RAW_MESSAGE)
    assert len(repo.all_articles()) == 1


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_enriched_article(
    article_id: str,
    sentiment_label: str = "neutral",
) -> EnrichedArticle:
    """Build a minimal EnrichedArticle for repository tests."""
    return EnrichedArticle(
        article_id=article_id,
        supplier_id=None,
        supplier_name_raw=None,
        title="Test article",
        url=f"https://reuters.com/{article_id}",
        published_at=_NOW,
        source_name="Reuters",
        sentiment_score=0.0,
        sentiment_label=sentiment_label,  # type: ignore[arg-type]
        topic_layoff=False,
        topic_bankruptcy=False,
        topic_strike=False,
        topic_disaster=False,
        topic_regulatory=False,
        source_credibility=1.0,
        word_count=10,
        processed_at=_NOW,
    )
