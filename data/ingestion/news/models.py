"""Pydantic v2 models for news ingestion pipeline.

Defines the schema for data flowing through the news ingestion pipeline:
    RawArticle      — raw article as received from NewsAPI or GDELT
    EnrichedArticle — article after NLP processing (sentiment, topics, entity)
    NewsRawEvent    — Kafka message schema for the raw.news topic
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RawArticle(BaseModel):
    """Raw article as received from NewsAPI or GDELT.

    article_id is always sha256(url) — deterministic and deduplication-safe.
    content may be None for sources that only expose title + snippet.
    source_credibility is assigned by domain at ingestion time, not by the source itself.
    """

    article_id: str  # sha256 of URL
    url: str
    title: str
    content: str | None  # None from sources that truncate body
    published_at: datetime
    source_name: str
    source_credibility: float = Field(ge=0.0, le=1.0)
    ingested_at: datetime
    ingestion_source: Literal["newsapi", "gdelt", "rss"]

    @field_validator("source_credibility")
    @classmethod
    def credibility_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"source_credibility must be 0.0–1.0, got {v}")
        return v


class EnrichedArticle(BaseModel):
    """Article after NLP processing.

    supplier_id is None if entity resolution failed — consumers must handle this.
    sentiment_score is in [-1.0, 1.0]: negative = bad news, positive = good news.
    Topic flags are keyword-matched; a full ML classifier is planned for Phase 2.
    """

    article_id: str
    supplier_id: str | None  # None if entity resolution failed
    supplier_name_raw: str | None  # raw company name extracted from text
    title: str
    url: str
    published_at: datetime
    source_name: str
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_label: Literal["positive", "negative", "neutral"]
    topic_layoff: bool
    topic_bankruptcy: bool
    topic_strike: bool
    topic_disaster: bool
    topic_regulatory: bool
    source_credibility: float = Field(ge=0.0, le=1.0)
    word_count: int = Field(ge=0)
    processed_at: datetime

    @field_validator("sentiment_score")
    @classmethod
    def sentiment_in_range(cls, v: float) -> float:
        if not -1.0 <= v <= 1.0:
            raise ValueError(f"sentiment_score must be -1.0–1.0, got {v}")
        return v


class NewsRawEvent(BaseModel):
    """Schema for the raw.news Kafka topic.

    Published by NewsAPIClient / GDELTClient after fetching each article.
    Consumed by NewsEnrichmentConsumer for NLP processing.
    content may be None — consumers must not assume it is populated.
    """

    source: Literal["newsapi", "gdelt"]
    article_id: str
    url: str
    title: str
    content: str | None
    published_at: datetime
    source_name: str
    ingested_at: datetime
