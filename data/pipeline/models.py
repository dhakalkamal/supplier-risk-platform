"""Shared Pydantic v2 models for the ingestion and entity resolution pipeline."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SupplierRegistryEntry(BaseModel):
    supplier_id: str  # internal UUID, prefix "sup_"
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)  # known alternative names
    country: str  # ISO 3166-1 alpha-2
    industry_code: str | None = None
    duns_number: str | None = None
    website: str | None = None
    created_at: datetime
    updated_at: datetime


class ResolutionResult(BaseModel):
    raw_name: str  # input string
    country_hint: str | None
    resolved: bool
    supplier_id: str | None = None  # None if unresolved
    canonical_name: str | None = None
    confidence: float  # 0.0–1.0
    method: Literal["exact", "alias", "fuzzy", "llm", "unresolved"]
    matched_string: str | None = None  # what we actually matched against
    resolved_at: datetime


class UnresolvedEntity(BaseModel):
    raw_name: str
    country_hint: str | None = None
    source: str  # "news", "sec", "ais"
    context: str | None = None  # sentence/snippet where name appeared
    attempted_at: datetime
    attempts: int = 1  # how many times resolution was tried
