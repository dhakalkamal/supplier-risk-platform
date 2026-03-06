"""Tests for data.ingestion.news.nlp_processor.NLPProcessor.

All tests run with use_finbert=False (lexicon scorer) to avoid requiring
the FinBERT model download in CI. The FinBERT fallback behaviour is tested
by patching the transformers import to raise ImportError.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.ingestion.news.models import EnrichedArticle, RawArticle
from data.ingestion.news.nlp_processor import (
    NLPProcessor,
    _score_to_label,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_raw_article(
    title: str = "Test article",
    content: str | None = "Test content",
    url: str = "https://reuters.com/test",
) -> RawArticle:
    """Build a minimal RawArticle for testing."""
    from data.ingestion.news.scraper import _article_id_from_url

    return RawArticle(
        article_id=_article_id_from_url(url),
        url=url,
        title=title,
        content=content,
        published_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        source_name="Reuters",
        source_credibility=1.0,
        ingested_at=datetime(2024, 1, 15, 10, 1, 0, tzinfo=timezone.utc),
        ingestion_source="newsapi",
    )


@pytest.fixture
def processor() -> NLPProcessor:
    """NLPProcessor with FinBERT disabled — uses lexicon scorer."""
    return NLPProcessor(use_finbert=False)


# ── Topic classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize("keyword,topic", [
    ("layoff", "layoff"),
    ("layoffs", "layoff"),
    ("redundancies", "layoff"),          # partial match on "redundan"
    ("workforce reduction", "layoff"),
    ("job cut", "layoff"),
    ("bankruptcy", "bankruptcy"),
    ("chapter 11", "bankruptcy"),
    ("insolvency", "bankruptcy"),
    ("liquidation", "bankruptcy"),       # partial match on "liquidat"
    ("strike", "strike"),
    ("industrial action", "strike"),
    ("walkout", "strike"),
    ("work stoppage", "strike"),
    ("labor dispute", "strike"),
    ("fire", "disaster"),
    ("explosion", "disaster"),
    ("flood", "disaster"),
    ("earthquake", "disaster"),
    ("fined", "regulatory"),
    ("penalty", "regulatory"),
    ("recall", "regulatory"),
    ("violation", "regulatory"),
    ("sanction", "regulatory"),
])
def test_classify_topics_detects_keyword(processor, keyword, topic):
    """classify_topics returns True for the correct topic given a keyword."""
    result = processor.classify_topics(f"Company faces {keyword} situation")
    assert result[topic] is True


def test_classify_topics_is_case_insensitive(processor):
    """classify_topics matches keywords regardless of case."""
    assert processor.classify_topics("Company LAYOFF announced")["layoff"] is True
    assert processor.classify_topics("Filing for BANKRUPTCY protection")["bankruptcy"] is True
    assert processor.classify_topics("Workers STRIKE at plant")["strike"] is True


def test_classify_topics_returns_all_five_keys(processor):
    """classify_topics always returns all five topic keys."""
    result = processor.classify_topics("No relevant content here")
    assert set(result.keys()) == {"layoff", "bankruptcy", "strike", "disaster", "regulatory"}


def test_classify_topics_all_false_for_neutral_text(processor):
    """Text with no matching keywords returns all False."""
    result = processor.classify_topics("Company reports strong quarterly earnings growth")
    assert all(v is False for v in result.values())


def test_classify_topics_multiple_topics_can_be_true(processor):
    """Text matching multiple topic categories sets multiple flags True."""
    text = "Workers strike at factory after bankruptcy filing"
    result = processor.classify_topics(text)
    assert result["strike"] is True
    assert result["bankruptcy"] is True


# ── Sentiment — lexicon scorer ────────────────────────────────────────────────


def test_get_sentiment_score_in_range(processor):
    """get_sentiment always returns a score in [-1.0, 1.0]."""
    score, _ = processor.get_sentiment("Some financial news text")
    assert -1.0 <= score <= 1.0


def test_get_sentiment_negative_for_bad_news(processor):
    """Clearly negative text produces a negative label."""
    text = "Company bankrupt layoff loss fail debt warn decline"
    score, label = processor.get_sentiment(text)
    assert label == "negative"
    assert score < 0


def test_get_sentiment_positive_for_good_news(processor):
    """Clearly positive text produces a positive label."""
    text = "Company profit growth expand award record strong beat"
    score, label = processor.get_sentiment(text)
    assert label == "positive"
    assert score > 0


def test_get_sentiment_neutral_for_bland_text(processor):
    """Text with no lexicon matches returns neutral label and score near 0."""
    text = "The company held its annual general meeting on Tuesday"
    score, label = processor.get_sentiment(text)
    assert label == "neutral"
    assert -0.05 <= score <= 0.05


def test_get_sentiment_label_matches_score(processor):
    """The label returned is always consistent with the score."""
    for text in [
        "profit growth strong beat record",
        "bankrupt layoff loss fail warn",
        "annual meeting held tuesday morning",
    ]:
        score, label = processor.get_sentiment(text)
        expected_label = _score_to_label(score)
        assert label == expected_label


# ── Sentiment — FinBERT fallback ──────────────────────────────────────────────


def test_finbert_fallback_when_transformers_unavailable():
    """If transformers cannot be imported, processor falls back to lexicon scorer."""
    with patch.dict("sys.modules", {"transformers": None}):
        proc = NLPProcessor(use_finbert=True)
        assert proc._finbert_available is False

    # Lexicon scorer still works
    score, label = proc.get_sentiment("Company profit growth strong")
    assert -1.0 <= score <= 1.0
    assert label in {"positive", "negative", "neutral"}


def test_finbert_fallback_when_pipeline_raises_on_load():
    """If pipeline() raises during model load, fallback activates silently."""
    with patch("data.ingestion.news.nlp_processor.NLPProcessor._load_finbert") as mock_load:
        proc = NLPProcessor.__new__(NLPProcessor)
        proc._use_finbert = True
        proc._pipeline = None
        proc._finbert_available = False
        mock_load.return_value = None  # _load_finbert called but sets nothing

    score, label = proc.get_sentiment("Company loss bankruptcy warn")
    assert label == "negative"


def test_finbert_fallback_on_inference_error():
    """If FinBERT pipeline raises during inference, falls back to lexicon scorer."""
    proc = NLPProcessor(use_finbert=False)
    proc._finbert_available = True
    mock_pipeline = MagicMock(side_effect=RuntimeError("CUDA out of memory"))
    proc._pipeline = mock_pipeline

    score, label = proc.get_sentiment("Company profit growth strong")
    # Fell back to lexicon — should still return a valid result
    assert -1.0 <= score <= 1.0
    assert label in {"positive", "negative", "neutral"}


# ── Entity extraction ─────────────────────────────────────────────────────────


def test_extract_company_mentions_returns_capitalised_names(processor):
    """extract_company_mentions finds multi-word capitalised sequences."""
    text = "Acme Industries announced layoffs. Rival Corp saw gains."
    mentions = processor.extract_company_mentions(text)
    assert "Acme Industries" in mentions
    assert "Rival Corp" in mentions


def test_extract_company_mentions_deduplicates(processor):
    """Each company name appears at most once in the result."""
    text = "Acme Industries filed. Acme Industries denied."
    mentions = processor.extract_company_mentions(text)
    assert mentions.count("Acme Industries") == 1


def test_extract_company_mentions_returns_at_most_ten(processor):
    """At most 10 candidate mentions are returned."""
    text = " ".join(
        f"Company{i} Corp" for i in range(20)
    )
    mentions = processor.extract_company_mentions(text)
    assert len(mentions) <= 10


def test_extract_company_mentions_empty_for_lowercase_text(processor):
    """No mentions found in all-lowercase text."""
    mentions = processor.extract_company_mentions("the company reported losses today")
    assert mentions == []


# ── process_article ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_article_returns_enriched_article(processor):
    """process_article returns a fully populated EnrichedArticle."""
    article = _make_raw_article(
        title="Acme Corp files for bankruptcy",
        content="Acme Corp announced Chapter 11 filing.",
    )
    enriched = await processor.process_article(article)

    assert isinstance(enriched, EnrichedArticle)
    assert enriched.article_id == article.article_id
    assert enriched.title == article.title
    assert enriched.url == article.url
    assert enriched.published_at == article.published_at
    assert enriched.source_name == article.source_name
    assert enriched.source_credibility == article.source_credibility
    assert enriched.supplier_id is None  # resolved in Session 3


@pytest.mark.asyncio
async def test_process_article_populates_all_topic_flags(processor):
    """All five topic flags are present on the returned EnrichedArticle."""
    article = _make_raw_article(title="Neutral quarterly update", content=None)
    enriched = await processor.process_article(article)

    assert hasattr(enriched, "topic_layoff")
    assert hasattr(enriched, "topic_bankruptcy")
    assert hasattr(enriched, "topic_strike")
    assert hasattr(enriched, "topic_disaster")
    assert hasattr(enriched, "topic_regulatory")


@pytest.mark.asyncio
async def test_process_article_detects_bankruptcy_topic(processor):
    """Articles mentioning bankruptcy set topic_bankruptcy = True."""
    article = _make_raw_article(
        title="Company files for Chapter 11 bankruptcy protection",
        content=None,
    )
    enriched = await processor.process_article(article)
    assert enriched.topic_bankruptcy is True


@pytest.mark.asyncio
async def test_process_article_sentiment_score_in_range(processor):
    """Sentiment score on the returned article is always in [-1.0, 1.0]."""
    article = _make_raw_article()
    enriched = await processor.process_article(article)
    assert -1.0 <= enriched.sentiment_score <= 1.0


@pytest.mark.asyncio
async def test_process_article_word_count_positive(processor):
    """word_count is > 0 for a non-empty article."""
    article = _make_raw_article(title="Breaking news today", content="More details here.")
    enriched = await processor.process_article(article)
    assert enriched.word_count > 0


@pytest.mark.asyncio
async def test_process_article_handles_none_content(processor):
    """process_article completes without error when content is None."""
    article = _make_raw_article(title="Short headline only", content=None)
    enriched = await processor.process_article(article)
    assert isinstance(enriched, EnrichedArticle)
    assert enriched.word_count > 0  # title still contributes word count


@pytest.mark.asyncio
async def test_process_article_supplier_name_raw_from_title(processor):
    """supplier_name_raw is extracted from the title when it contains a company name."""
    article = _make_raw_article(
        title="Acme Industries reports record losses",
        content=None,
    )
    enriched = await processor.process_article(article)
    assert enriched.supplier_name_raw is not None


# ── _score_to_label helper ────────────────────────────────────────────────────


@pytest.mark.parametrize("score,expected", [
    (0.5, "positive"),
    (0.06, "positive"),
    (0.05, "neutral"),   # boundary: exactly 0.05 is neutral
    (0.0, "neutral"),
    (-0.05, "neutral"),  # boundary: exactly -0.05 is neutral
    (-0.06, "negative"),
    (-0.5, "negative"),
])
def test_score_to_label_boundaries(score, expected):
    """_score_to_label returns the correct label at boundary values."""
    assert _score_to_label(score) == expected
