"""NLP processing for raw news articles.

NLPProcessor enriches a RawArticle into an EnrichedArticle by running:
  1. Sentiment analysis  — FinBERT (ProsusAI/finbert) with lexicon fallback
  2. Topic classification — keyword matching (5 categories)
  3. Entity extraction   — simple NER returning candidate company name strings
                           (full entity resolution happens in Session 3)

FinBERT is loaded lazily on first use. If the model fails to load for any
reason (network, disk, CUDA OOM), the processor falls back silently to a
lexicon-based count scorer. NLP never blocks on model availability.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from data.ingestion.news.models import EnrichedArticle, RawArticle

log = structlog.get_logger()

# ── Topic keywords ─────────────────────────────────────────────────────────────

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "layoff": ["layoff", "layoffs", "redundan", "workforce reduction", "job cut", "retrench"],
    "bankruptcy": ["bankruptcy", "chapter 11", "insolvency", "administration", "liquidat"],
    "strike": ["strike", "industrial action", "walkout", "work stoppage", "labor dispute"],
    "disaster": ["fire", "explosion", "flood", "earthquake", "hurricane", "facility damage"],
    "regulatory": ["fined", "penalty", "recall", "shutdown order", "violation", "sanction"],
}

# ── Lexicon fallback ───────────────────────────────────────────────────────────

NEGATIVE_WORDS: frozenset[str] = frozenset(
    ["bankrupt", "layoff", "loss", "decline", "fail", "risk", "debt", "warn"]
)
POSITIVE_WORDS: frozenset[str] = frozenset(
    ["profit", "growth", "expand", "award", "record", "strong", "beat"]
)

# ── FinBERT label → float mapping ─────────────────────────────────────────────

_FINBERT_LABEL_TO_SCORE: dict[str, float] = {
    "positive": 1.0,
    "negative": -1.0,
    "neutral": 0.0,
}

# Maximum token length FinBERT accepts
_FINBERT_MAX_TOKENS = 512

# Simple NER: capitalised multi-word sequences that look like company names
_COMPANY_PATTERN = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,4})\b")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── NLPProcessor ──────────────────────────────────────────────────────────────


class NLPProcessor:
    """Processes raw articles: sentiment, topic classification, entity linking.

    Uses FinBERT for sentiment (ProsusAI/finbert).
    Falls back to lexicon-based scorer if model unavailable.
    Topic classification uses keyword matching (ML classifier planned for Phase 2).
    Entity linking is a simple regex NER — full resolution pipeline in Session 3.

    Args:
        use_finbert: If False, skip FinBERT and always use the lexicon scorer.
                     Useful for tests and offline environments.
    """

    def __init__(self, use_finbert: bool = True) -> None:
        self._use_finbert = use_finbert
        self._pipeline: Any = None  # transformers pipeline, loaded lazily
        self._finbert_available: bool = False
        if use_finbert:
            self._load_finbert()

    def _load_finbert(self) -> None:
        """Attempt to load FinBERT. On any failure, fall back to lexicon scorer."""
        try:
            from transformers import pipeline

            self._pipeline = pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                truncation=True,
                max_length=_FINBERT_MAX_TOKENS,
            )
            self._finbert_available = True
            log.info("nlp.finbert_loaded")
        except Exception as exc:
            self._finbert_available = False
            log.warning(
                "nlp.finbert_unavailable",
                error=str(exc),
                fallback="lexicon_scorer",
            )

    async def process_article(self, article: RawArticle) -> EnrichedArticle:
        """Enrich a RawArticle with sentiment, topics, and entity candidates.

        Args:
            article: Raw article from NewsAPI or GDELT.

        Returns:
            EnrichedArticle with all NLP fields populated.
        """
        text = _build_analysis_text(article)
        sentiment_score, sentiment_label = self.get_sentiment(text)
        topics = self.classify_topics(text)
        company_mentions = self.extract_company_mentions(text)
        supplier_name_raw = company_mentions[0] if company_mentions else None

        return EnrichedArticle(
            article_id=article.article_id,
            supplier_id=None,  # resolved in Session 3
            supplier_name_raw=supplier_name_raw,
            title=article.title,
            url=article.url,
            published_at=article.published_at,
            source_name=article.source_name,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            topic_layoff=topics["layoff"],
            topic_bankruptcy=topics["bankruptcy"],
            topic_strike=topics["strike"],
            topic_disaster=topics["disaster"],
            topic_regulatory=topics["regulatory"],
            source_credibility=article.source_credibility,
            word_count=len(text.split()),
            processed_at=_utcnow(),
        )

    def get_sentiment(
        self, text: str
    ) -> tuple[float, Literal["positive", "negative", "neutral"]]:
        """Return (score, label) for the given text.

        Uses FinBERT if available, otherwise falls back to lexicon scorer.
        Score is in [-1.0, 1.0]. Label is one of: positive, negative, neutral.

        Args:
            text: Text to analyse (title + content, or title alone).

        Returns:
            Tuple of (float score, str label).
        """
        if self._finbert_available and self._pipeline is not None:
            return self._finbert_sentiment(text)
        return self._lexicon_sentiment(text)

    def _finbert_sentiment(
        self, text: str
    ) -> tuple[float, Literal["positive", "negative", "neutral"]]:
        """Run FinBERT inference. Falls back to lexicon on runtime error."""
        try:
            result = self._pipeline(text[:2000])
            if isinstance(result, list) and result:
                top = result[0]
                raw_label: str = top.get("label", "neutral").lower()
                score: float = _FINBERT_LABEL_TO_SCORE.get(raw_label, 0.0)
                # Scale by model confidence so uncertain predictions land near 0
                confidence: float = float(top.get("score", 1.0))
                scaled = score * confidence
                return round(scaled, 4), _score_to_label(scaled)
        except Exception as exc:
            log.warning("nlp.finbert_inference_failed", error=str(exc))
        return self._lexicon_sentiment(text)

    def _lexicon_sentiment(
        self, text: str
    ) -> tuple[float, Literal["positive", "negative", "neutral"]]:
        """Simple count-based lexicon scorer.

        Score = (positive_count - negative_count) / max(total_words, 1)
        Clamped to [-1.0, 1.0].
        """
        words = text.lower().split()
        total = max(len(words), 1)
        positive_count = sum(1 for w in words if w in POSITIVE_WORDS)
        negative_count = sum(1 for w in words if w in NEGATIVE_WORDS)
        raw_score = (positive_count - negative_count) / total
        score = max(-1.0, min(1.0, raw_score))
        label = _score_to_label(score)
        return round(score, 4), label

    def classify_topics(self, text: str) -> dict[str, bool]:
        """Return topic flags for the given text using keyword matching.

        Matching is case-insensitive. Partial keyword matches are included
        (e.g. "redundancies" matches "redundan").

        Args:
            text: Text to classify.

        Returns:
            Dict with keys: layoff, bankruptcy, strike, disaster, regulatory.
        """
        lowered = text.lower()
        return {
            topic: any(keyword in lowered for keyword in keywords)
            for topic, keywords in TOPIC_KEYWORDS.items()
        }

    def extract_company_mentions(self, text: str) -> list[str]:
        """Extract candidate company name strings from text using simple NER.

        Matches capitalised multi-word sequences (e.g. "Acme Industries").
        This is intentionally lightweight — full entity resolution is built
        in Session 3. Returns up to 10 candidates to keep output manageable.

        Args:
            text: Text to scan for company mentions.

        Returns:
            List of candidate company name strings (may be empty).
        """
        matches = _COMPANY_PATTERN.findall(text)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for match in matches:
            if match not in seen:
                seen.add(match)
                unique.append(match)
        return unique[:10]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_analysis_text(article: RawArticle) -> str:
    """Concatenate title and content for NLP analysis.

    Content may be None (GDELT, truncated sources). In that case, the title
    alone is used. Missing content is not treated as empty string.
    """
    if article.content:
        return f"{article.title}. {article.content}"
    return article.title


def _score_to_label(score: float) -> Literal["positive", "negative", "neutral"]:
    """Convert a numeric sentiment score to a categorical label.

    Thresholds: negative < -0.05, positive > 0.05, otherwise neutral.
    Small scores near zero are treated as neutral to reduce false positives
    from the lexicon scorer.
    """
    if score > 0.05:
        return "positive"
    if score < -0.05:
        return "negative"
    return "neutral"
