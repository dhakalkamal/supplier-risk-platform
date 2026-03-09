"""
Pydantic validation schemas for all raw Kafka topic messages.
Topics: raw.sec, raw.news, raw.ais, raw.macro, raw.geo
DLQ:    raw.dlq.{source}
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional, Any
from pydantic import BaseModel, HttpUrl, field_validator, model_validator
import re


# ─────────────────────────────────────────────────────────────────────────────
# raw.sec  →  pipeline.raw_sec_filings
# ─────────────────────────────────────────────────────────────────────────────

class SECRawEvent(BaseModel):
    cik: str
    company_name: str
    filing_type: str                    # e.g. "10-K", "8-K", "10-Q"
    filed_date: date
    period_of_report: Optional[date]
    financials: dict[str, Any]          # raw XBRL / JSON financials blob
    going_concern: bool = False
    ingested_at: datetime

    @field_validator("cik")
    @classmethod
    def cik_format(cls, v: str) -> str:
        v = v.strip().lstrip("0") or "0"
        if not re.fullmatch(r"\d{1,10}", v):
            raise ValueError(f"Invalid CIK: {v!r}")
        return v

    @field_validator("filing_type")
    @classmethod
    def known_filing_type(cls, v: str) -> str:
        allowed = {"10-K", "10-Q", "8-K", "20-F", "6-K", "DEF 14A", "SC 13G", "SC 13D"}
        if v not in allowed:
            raise ValueError(f"Unknown filing_type {v!r}; expected one of {allowed}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# raw.news  →  pipeline.raw_news_articles
# ─────────────────────────────────────────────────────────────────────────────

class NewsRawEvent(BaseModel):
    article_id: str                     # UUID from upstream source
    url: str
    title: str
    content: str
    published_at: datetime
    source_name: str
    ingestion_source: str               # "newsapi" | "gdelt"
    ingested_at: datetime

    @field_validator("article_id")
    @classmethod
    def non_empty_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("article_id must not be blank")
        return v.strip()

    @field_validator("ingestion_source")
    @classmethod
    def valid_ingestion_source(cls, v: str) -> str:
        allowed = {"newsapi", "gdelt"}
        if v not in allowed:
            raise ValueError(f"ingestion_source must be one of {allowed}, got {v!r}")
        return v

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# raw.ais  →  pipeline.raw_ais_events
# ─────────────────────────────────────────────────────────────────────────────

class AISRawEvent(BaseModel):
    port_id: str
    port_name: str
    vessel_mmsi: str                    # Maritime Mobile Service Identity (9 digits)
    vessel_name: str
    arrival_time: Optional[datetime]
    departure_time: Optional[datetime]
    cargo_type: Optional[str]
    ingested_at: datetime

    @field_validator("vessel_mmsi")
    @classmethod
    def mmsi_format(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"\d{9}", v):
            raise ValueError(f"MMSI must be exactly 9 digits, got {v!r}")
        return v

    @model_validator(mode="after")
    def arrival_before_departure(self) -> AISRawEvent:
        if self.arrival_time and self.departure_time:
            if self.arrival_time > self.departure_time:
                raise ValueError(
                    f"arrival_time {self.arrival_time} is after departure_time {self.departure_time}"
                )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# raw.macro  →  pipeline.raw_macro_series
# ─────────────────────────────────────────────────────────────────────────────

class MacroRawEvent(BaseModel):
    series_id: str                      # FRED series ID, e.g. "GDP", "UNRATE"
    series_name: str
    observation_date: date
    value: float
    unit: str
    ingested_at: datetime

    @field_validator("value")
    @classmethod
    def finite_value(cls, v: float) -> float:
        import math
        if not math.isfinite(v):
            raise ValueError(f"value must be finite, got {v}")
        return v

    @field_validator("series_id")
    @classmethod
    def series_id_format(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("series_id must not be blank")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# raw.geo  →  pipeline.raw_geo_events
# ─────────────────────────────────────────────────────────────────────────────

_GEO_SEVERITIES = {"low", "medium", "high", "critical"}
_GEO_SOURCES = {"acled", "gdelt", "noaa", "ofac"}

class GeoRawEvent(BaseModel):
    event_id: str
    event_type: str                     # e.g. "conflict", "weather", "sanctions"
    country: str                        # ISO 3166-1 alpha-2
    region: Optional[str]
    event_date: date
    severity: str
    source: str                         # "acled" | "gdelt" | "noaa" | "ofac"
    ingested_at: datetime

    @field_validator("country")
    @classmethod
    def iso_country(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError(f"country must be ISO 3166-1 alpha-2 (2 uppercase letters), got {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, v: str) -> str:
        v = v.lower()
        if v not in _GEO_SEVERITIES:
            raise ValueError(f"severity must be one of {_GEO_SEVERITIES}, got {v!r}")
        return v

    @field_validator("source")
    @classmethod
    def valid_source(cls, v: str) -> str:
        v = v.lower()
        if v not in _GEO_SOURCES:
            raise ValueError(f"source must be one of {_GEO_SOURCES}, got {v!r}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Dead Letter Queue — raw.dlq.{source}
# ─────────────────────────────────────────────────────────────────────────────

class DeadLetterEvent(BaseModel):
    original_topic: str
    original_payload: dict              # raw message that failed
    error_type: str                     # exception class name
    error_message: str
    failed_at: datetime
    retry_count: int
    source: str                         # which ingestion source


# ─────────────────────────────────────────────────────────────────────────────
# Registry: topic name → schema class
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "raw.sec":   SECRawEvent,
    "raw.news":  NewsRawEvent,
    "raw.ais":   AISRawEvent,
    "raw.macro": MacroRawEvent,
    "raw.geo":   GeoRawEvent,
}
