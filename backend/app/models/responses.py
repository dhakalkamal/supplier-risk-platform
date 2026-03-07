"""Pydantic v2 response models for all API endpoints.

All responses use one of two envelopes (API_SPEC.md Section 4):
  - DataResponse[T]  — single object: {"data": {...}}
  - ListResponse[T]  — paginated list: {"data": [...], "meta": {...}}

Entity-specific models are defined below the envelope types.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


# =============================================================================
# Standard response envelopes
# =============================================================================


class Meta(BaseModel):
    """Pagination metadata included in every list response."""

    total: int
    page: int
    per_page: int
    total_pages: int


class DataResponse(BaseModel, Generic[T]):
    """Single-object response envelope: {"data": {...}}"""

    data: T


class ListResponse(BaseModel, Generic[T]):
    """Paginated list response envelope: {"data": [...], "meta": {...}}"""

    data: list[T]
    meta: Meta


# =============================================================================
# Health & readiness
# =============================================================================


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    timestamp: datetime


class DependencyStatus(BaseModel):
    postgres: str
    redis: str
    kafka: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    dependencies: DependencyStatus
    timestamp: datetime


# =============================================================================
# Portfolio — summary (dashboard stats)
# =============================================================================


class PortfolioSummaryResponse(BaseModel):
    total_suppliers: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    unread_alerts_count: int
    average_portfolio_score: int
    score_trend_7d: Literal["improving", "worsening", "stable"]
    last_scored_at: datetime | None
    plan_supplier_limit: int | None  # None = unlimited (enterprise)
    plan_supplier_used: int


# =============================================================================
# Portfolio — supplier list row
# =============================================================================


class SupplierSummary(BaseModel):
    """One row in GET /api/v1/portfolio/suppliers response."""

    portfolio_supplier_id: str
    supplier_id: str
    canonical_name: str
    custom_name: str | None
    country: str
    industry_code: str | None
    industry_name: str | None
    internal_id: str | None
    tags: list[str]
    risk_score: int | None
    risk_level: Literal["low", "medium", "high"] | None
    score_7d_delta: int | None
    score_trend: Literal["increasing", "decreasing", "stable"] | None
    unread_alerts_count: int
    last_score_updated_at: datetime | None
    data_completeness: float | None
    added_to_portfolio_at: datetime


# =============================================================================
# Portfolio — add / patch responses
# =============================================================================


class AddSupplierResponse(BaseModel):
    """Response body for POST /api/v1/portfolio/suppliers (201)."""

    portfolio_supplier_id: str
    supplier_id: str
    canonical_name: str
    resolution_confidence: float | None
    resolution_method: str | None
    added_at: datetime


class PatchPortfolioSupplierResponse(BaseModel):
    """Response body for PATCH /api/v1/portfolio/suppliers/{id} (200)."""

    portfolio_supplier_id: str
    custom_name: str | None
    internal_id: str | None
    tags: list[str]
    updated_at: datetime


# =============================================================================
# Portfolio — bulk import
# =============================================================================


class ImportJobResponse(BaseModel):
    """Response body for POST /api/v1/portfolio/suppliers/import (202)."""

    import_id: str
    status: Literal["processing"]
    total_rows: int
    poll_url: str
    submitted_at: datetime


class UnresolvedImportItem(BaseModel):
    row: int
    raw_name: str
    country: str | None
    reason: str
    best_candidate: str | None
    best_confidence: float | None


class ImportStatusResponse(BaseModel):
    """Response body for GET /api/v1/portfolio/imports/{id} (200)."""

    import_id: str
    status: Literal["processing", "completed", "failed"]
    total_rows: int
    resolved_count: int
    added_count: int
    duplicate_count: int
    unresolved_count: int
    error_count: int
    plan_limit_skipped_count: int
    unresolved_items: list[UnresolvedImportItem]
    started_at: datetime
    completed_at: datetime | None


# =============================================================================
# Supplier profile (full) — nested sub-models
# =============================================================================


class SignalCategoryBreakdown(BaseModel):
    """Per-category breakdown in current_score.signal_breakdown."""

    score: int
    weight: float
    data_available: bool


class SignalDriver(BaseModel):
    """One entry in current_score.top_drivers."""

    signal_name: str
    display_name: str
    category: str
    contribution: int
    direction: Literal["increases_risk", "decreases_risk"]
    raw_value: float | None
    explanation: str


class CurrentScore(BaseModel):
    """Embedded current risk score inside SupplierProfile."""

    score: int
    risk_level: Literal["low", "medium", "high"]
    model_version: str
    scored_at: datetime
    data_completeness: float | None
    signal_breakdown: dict[str, SignalCategoryBreakdown]
    top_drivers: list[SignalDriver]


class PrimaryLocation(BaseModel):
    city: str | None
    country: str
    lat: float | None
    lng: float | None


class SupplierProfile(BaseModel):
    """Response body for GET /api/v1/suppliers/{id} (200)."""

    supplier_id: str
    canonical_name: str
    aliases: list[str]
    country: str
    industry_code: str | None
    industry_name: str | None
    duns_number: str | None
    cik: str | None
    website: str | None
    primary_location: PrimaryLocation | None
    is_public_company: bool
    in_portfolio: bool
    portfolio_supplier_id: str | None
    current_score: CurrentScore | None


# =============================================================================
# Score history
# =============================================================================


class ScoreHistoryItem(BaseModel):
    date: date
    score: int
    risk_level: Literal["low", "medium", "high"]
    model_version: str


class ScoreHistoryResponse(BaseModel):
    """Response body for GET /api/v1/suppliers/{id}/score-history."""

    supplier_id: str
    days_requested: int
    days_available: int
    scores: list[ScoreHistoryItem]


# =============================================================================
# Supplier news
# =============================================================================


class NewsArticleResponse(BaseModel):
    """One item in GET /api/v1/suppliers/{id}/news."""

    article_id: str
    title: str
    url: str
    source_name: str
    source_credibility: float | None
    published_at: datetime
    sentiment_score: float
    sentiment_label: Literal["positive", "negative", "neutral"]
    sentiment_model: str
    topics: list[str]
    score_contribution: int | None
    content_available: bool


# =============================================================================
# Entity resolution
# =============================================================================


class ResolveAlternative(BaseModel):
    supplier_id: str
    canonical_name: str
    country: str
    confidence: float


class ResolveSupplierResponse(BaseModel):
    """Response body for POST /api/v1/suppliers/resolve (200)."""

    resolved: bool
    supplier_id: str | None
    canonical_name: str | None
    country: str | None
    confidence: float
    match_method: str
    alternatives: list[ResolveAlternative]


# =============================================================================
# Alerts
# =============================================================================


class AlertResponse(BaseModel):
    """One item in GET /api/v1/alerts or PATCH /api/v1/alerts/{id}."""

    alert_id: str
    supplier_id: str
    supplier_name: str
    alert_type: Literal["score_spike", "high_threshold", "event_detected", "sanctions_hit"]
    severity: Literal["low", "medium", "high", "critical"]
    title: str
    message: str
    metadata: dict[str, Any]
    status: Literal["new", "investigating", "resolved", "dismissed"]
    note: str | None
    fired_at: datetime
    read_at: datetime | None
    resolved_at: datetime | None


class PatchAlertResponse(BaseModel):
    """Response body for PATCH /api/v1/alerts/{id} (200)."""

    alert_id: str
    status: Literal["new", "investigating", "resolved", "dismissed"]
    note: str | None
    updated_at: datetime


# =============================================================================
# Settings — alert rules
# =============================================================================


class EmailChannelResponse(BaseModel):
    enabled: bool
    recipients: list[str]


class SlackChannelResponse(BaseModel):
    enabled: bool
    webhook_url: str | None
    webhook_verified: bool


class WebhookChannelResponse(BaseModel):
    enabled: bool
    url: str | None
    secret: str | None


class ChannelsResponse(BaseModel):
    email: EmailChannelResponse
    slack: SlackChannelResponse
    webhook: WebhookChannelResponse


class AlertRulesResponse(BaseModel):
    """Response body for GET/PUT /api/v1/settings/alert-rules."""

    score_spike_threshold: int
    high_risk_threshold: int
    channels: ChannelsResponse
    updated_at: datetime


# =============================================================================
# Settings — users
# =============================================================================


class UserResponse(BaseModel):
    """One item in GET /api/v1/settings/users."""

    user_id: str
    email: str
    role: Literal["admin", "viewer"]
    created_at: datetime
    last_active_at: datetime | None


class InviteResponse(BaseModel):
    """Response body for POST /api/v1/settings/users/invite (201)."""

    invite_id: str
    email: str
    role: Literal["admin", "viewer"]
    expires_at: datetime
