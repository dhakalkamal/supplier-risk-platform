"""Pydantic v2 request models for all API endpoints.

Validation rules enforced here match API_SPEC.md Section 6 exactly.
These models are injected via FastAPI Depends() — never instantiated directly in routes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# =============================================================================
# Shared / reusable field helpers
# =============================================================================

_SORT_BY_VALUES = Literal["risk_score", "name", "last_updated", "date_added"]
_SORT_ORDER_VALUES = Literal["asc", "desc"]
_RISK_LEVEL_VALUES = Literal["low", "medium", "high"]
_ALERT_STATUS_VALUES = Literal["new", "investigating", "resolved", "dismissed", "all"]
_ALERT_SEVERITY_VALUES = Literal["low", "medium", "high", "critical"]
_ALERT_TYPE_VALUES = Literal[
    "score_spike", "high_threshold", "event_detected", "sanctions_hit"
]
_SENTIMENT_VALUES = Literal["positive", "negative", "neutral"]


def _strip_optional(v: str | None) -> str | None:
    return v.strip() if v is not None else None


# =============================================================================
# Portfolio — list / filter query params
# =============================================================================


class PortfolioSuppliersParams(BaseModel):
    """Query parameters for GET /api/v1/portfolio/suppliers."""

    model_config = ConfigDict(populate_by_name=True)

    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)
    sort_by: _SORT_BY_VALUES = "risk_score"
    sort_order: _SORT_ORDER_VALUES = "desc"
    risk_level: _RISK_LEVEL_VALUES | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    search: str | None = Field(default=None, max_length=200)
    tag: str | None = None

    @field_validator("country", mode="before")
    @classmethod
    def country_uppercase(cls, v: str | None) -> str | None:
        return v.upper() if v is not None else None

    @field_validator("search", mode="before")
    @classmethod
    def strip_search(cls, v: str | None) -> str | None:
        return _strip_optional(v)


# =============================================================================
# Portfolio — add supplier
# =============================================================================


class AddSupplierRequest(BaseModel):
    """Request body for POST /api/v1/portfolio/suppliers.

    Provide either supplier_id (known canonical ID) OR raw_name (triggers entity
    resolution) — not both, not neither.
    """

    supplier_id: str | None = None
    raw_name: str | None = Field(default=None, max_length=500)
    country_hint: str | None = Field(default=None, min_length=2, max_length=2)
    internal_id: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("country_hint", mode="before")
    @classmethod
    def country_hint_uppercase(cls, v: str | None) -> str | None:
        return v.upper() if v is not None else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        for tag in v:
            if len(tag) > 50:
                raise ValueError(f"Each tag must be ≤ 50 chars, got {len(tag)} chars.")
        return v

    @model_validator(mode="after")
    def exactly_one_identifier(self) -> "AddSupplierRequest":
        has_id = self.supplier_id is not None
        has_name = self.raw_name is not None
        if has_id and has_name:
            raise ValueError("Provide supplier_id OR raw_name — not both.")
        if not has_id and not has_name:
            raise ValueError("Provide either supplier_id or raw_name.")
        return self


# =============================================================================
# Portfolio — patch supplier metadata
# =============================================================================


class PatchPortfolioSupplierRequest(BaseModel):
    """Request body for PATCH /api/v1/portfolio/suppliers/{id}.

    All fields are optional — send only the fields to update.
    """

    custom_name: str | None = Field(default=None, max_length=255)
    internal_id: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=10)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for tag in v:
            if len(tag) > 50:
                raise ValueError(f"Each tag must be ≤ 50 chars, got {len(tag)} chars.")
        return v


# =============================================================================
# Alerts — list / filter query params
# =============================================================================


class AlertsListParams(BaseModel):
    """Query parameters for GET /api/v1/alerts."""

    model_config = ConfigDict(populate_by_name=True)

    status: _ALERT_STATUS_VALUES = "new"
    severity: _ALERT_SEVERITY_VALUES | None = None
    supplier_id: str | None = None
    alert_type: _ALERT_TYPE_VALUES | None = None
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)


# =============================================================================
# Alerts — patch status / note
# =============================================================================


class PatchAlertRequest(BaseModel):
    """Request body for PATCH /api/v1/alerts/{id}.

    State transition validation lives in the repository (not here).
    Valid transitions are defined in API_SPEC.md Section 7.4.
    """

    status: _ALERT_STATUS_VALUES | None = None
    note: str | None = Field(default=None, max_length=2000)


# =============================================================================
# Suppliers — score history query params
# =============================================================================


class ScoreHistoryParams(BaseModel):
    """Query parameters for GET /api/v1/suppliers/{id}/score-history."""

    model_config = ConfigDict(populate_by_name=True)

    days: int = Field(default=90, ge=1, le=365)


# =============================================================================
# Suppliers — news query params
# =============================================================================


class SupplierNewsParams(BaseModel):
    """Query parameters for GET /api/v1/suppliers/{id}/news."""

    model_config = ConfigDict(populate_by_name=True)

    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)
    sentiment: _SENTIMENT_VALUES | None = None
    days: int = Field(default=30, ge=1, le=90)


# =============================================================================
# Suppliers — entity resolution
# =============================================================================


class ResolveSupplierRequest(BaseModel):
    """Request body for POST /api/v1/suppliers/resolve."""

    name: str = Field(min_length=1, max_length=500)
    country_hint: str | None = Field(default=None, min_length=2, max_length=2)
    context: str | None = Field(default=None, max_length=1000)

    @field_validator("country_hint", mode="before")
    @classmethod
    def country_hint_uppercase(cls, v: str | None) -> str | None:
        return v.upper() if v is not None else None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


# =============================================================================
# Settings — alert rules channels (nested models)
# =============================================================================


class EmailChannelRequest(BaseModel):
    enabled: bool = False
    recipients: list[EmailStr] = Field(default_factory=list, max_length=10)


class SlackChannelRequest(BaseModel):
    enabled: bool = False
    webhook_url: str | None = None

    @field_validator("webhook_url")
    @classmethod
    def webhook_must_be_https(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("Slack webhook URL must start with https://")
        return v


class WebhookChannelRequest(BaseModel):
    enabled: bool = False
    url: str | None = None
    secret: str | None = None

    @field_validator("url")
    @classmethod
    def url_must_be_https(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("Webhook URL must start with https://")
        return v


class ChannelsRequest(BaseModel):
    email: EmailChannelRequest = Field(default_factory=EmailChannelRequest)
    slack: SlackChannelRequest = Field(default_factory=SlackChannelRequest)
    webhook: WebhookChannelRequest = Field(default_factory=WebhookChannelRequest)


class AlertRulesRequest(BaseModel):
    """Request body for PUT /api/v1/settings/alert-rules. Admin only."""

    score_spike_threshold: int = Field(default=15, ge=5, le=50)
    high_risk_threshold: int = Field(default=70, ge=50, le=95)
    channels: ChannelsRequest = Field(default_factory=ChannelsRequest)


# =============================================================================
# Settings — user management
# =============================================================================


class InviteUserRequest(BaseModel):
    """Request body for POST /api/v1/settings/users/invite. Admin only."""

    email: EmailStr
    role: Literal["admin", "viewer"]
