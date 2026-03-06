"""Score output models for the supplier risk scoring pipeline.

These models define the contract between the heuristic scorer (v0) and the
future XGBoost model (v1). The ML model is a drop-in replacement — same inputs,
same output format. Neither the API nor the alert engine should need to change
when v1 ships.

See ML_SPEC.md Section 6.1 and ADR-014 for the frozen schema rationale.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalContribution(BaseModel):
    """Attribution for a single signal's contribution to the risk score.

    Mirrors what SHAP provides for the ML model — the heuristic scorer produces
    the same structure so the UI and API never need to distinguish between them.
    """

    model_config = ConfigDict(frozen=True)

    signal_name: str  # e.g. "altman_z_score" — matches SupplierFeatureVector field name
    display_name: str  # e.g. "Financial Stress Index (Altman Z')"
    category: Literal["financial", "news", "shipping", "geopolitical", "macro"]
    raw_value: float | None  # the actual feature value; None if signal was missing
    contribution: float  # points added to the category score (can be negative)
    direction: Literal["increases_risk", "decreases_risk", "neutral"]
    explanation: str  # e.g. "Altman Z-Score of 1.1 is in distress zone (< 1.23)"

    @field_validator("contribution")
    @classmethod
    def contribution_bounded(cls, v: float) -> float:
        if not (-100.0 <= v <= 100.0):
            raise ValueError(f"contribution must be in [-100, 100], got {v}")
        return v


class RiskScoreOutput(BaseModel):
    """Complete risk score output — identical schema for heuristic v0 and XGBoost v1.

    The ML model is a drop-in replacement for the heuristic scorer.
    Neither the API nor the Kafka consumer should branch on model_version.

    top_drivers: top 5 signals by abs(contribution), for UI display.
    all_signals: full list sorted by abs(contribution) descending, for audit/SHAP export.
    """

    model_config = ConfigDict(frozen=True)

    supplier_id: str
    score: int  # 0–100, higher = more risk
    risk_level: Literal["low", "medium", "high"]

    # Category sub-scores — 0–100 each, weighted to produce final score
    financial_score: float
    news_score: float
    shipping_score: float
    geo_score: float
    macro_score: float

    # Signal attribution (SHAP-equivalent for heuristic model)
    top_drivers: list[SignalContribution]  # top 5 by abs(contribution)
    all_signals: list[SignalContribution]  # all signals, sorted by abs(contribution) desc

    # Metadata
    model_version: str  # "heuristic_v0" or "xgboost_v1"
    feature_date: date  # date of the feature snapshot used
    scored_at: datetime
    data_completeness: float  # 0.0–1.0, fraction of expected signals that were non-None

    @field_validator("score")
    @classmethod
    def score_bounded(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError(f"score must be 0–100, got {v}")
        return v

    @field_validator("financial_score", "news_score", "shipping_score", "geo_score", "macro_score")
    @classmethod
    def category_score_bounded(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"category score must be 0–100, got {v}")
        return v

    @field_validator("data_completeness")
    @classmethod
    def completeness_bounded(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"data_completeness must be 0.0–1.0, got {v}")
        return v

    @field_validator("top_drivers")
    @classmethod
    def top_drivers_max_five(cls, v: list[SignalContribution]) -> list[SignalContribution]:
        if len(v) > 5:
            raise ValueError(f"top_drivers must have at most 5 entries, got {len(v)}")
        return v


class DailyScoreRecord(BaseModel):
    """Row written to scores.supplier_daily_scores.

    signal_breakdown stores the full RiskScoreOutput as JSON so that the
    complete attribution is available for audit without joining other tables.
    Upserted on (supplier_id, feature_date) — one record per supplier per day.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    supplier_id: str
    score: int
    risk_level: str
    signal_breakdown: dict[str, object]  # full RiskScoreOutput serialised via .model_dump()
    model_version: str
    feature_date: date
    scored_at: datetime

    @field_validator("score")
    @classmethod
    def score_bounded(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError(f"score must be 0–100, got {v}")
        return v

    @classmethod
    def from_score_output(cls, output: RiskScoreOutput) -> "DailyScoreRecord":
        """Construct a DailyScoreRecord from a RiskScoreOutput."""
        return cls(
            supplier_id=output.supplier_id,
            score=output.score,
            risk_level=output.risk_level,
            signal_breakdown=output.model_dump(mode="json"),
            model_version=output.model_version,
            feature_date=output.feature_date,
            scored_at=output.scored_at,
        )
