"""Tests for ml.scoring — heuristic scorer, models, and in-memory repository.

Coverage target: ≥85% on ml/scoring/.

No database or Kafka connections required — InMemoryScoreRepository used
for all persistence assertions.

Tests are organised by:
    - Scorer: high-risk, low-risk, missing data, sanctions, score bounds
    - Output schema validation (matches ML_SPEC.md contract)
    - Data completeness computation
    - Model version tag
    - Score output models (DailyScoreRecord factory)
    - InMemoryScoreRepository (upsert, get_latest, get_history)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ml.features.feature_vector import SupplierFeatureVector
from ml.scoring.heuristic_scorer import _NULLABLE_FEATURE_COLUMNS, HeuristicRiskScorer
from ml.scoring.models import DailyScoreRecord
from ml.scoring.score_repository import InMemoryScoreRepository

# ── Module-level scorer (stateless — safe to share across tests) ──────────────

_SCORER = HeuristicRiskScorer()
_FEATURE_DATE = date(2026, 3, 6)
_SCORED_AT = datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixture helpers ───────────────────────────────────────────────────────────


# Default field values that trigger zero risk points in every category.
# All optional signals are in the "safe" range. Override per-test to add risk.
_FV_DEFAULTS: dict[str, object] = {
    "supplier_id": "sup_test_001",
    "feature_date": _FEATURE_DATE,
    # Financial — safe zone
    "altman_z_score": 3.5,
    "altman_working_capital_ratio": 0.30,
    "altman_retained_earnings_ratio": 0.20,
    "altman_ebit_ratio": 0.10,
    "altman_equity_to_debt": 1.50,
    "altman_revenue_ratio": 1.00,
    "going_concern_flag": False,
    "current_ratio": 2.00,
    "quick_ratio": 1.50,
    "cash_ratio": 0.50,
    "debt_to_equity": 0.80,
    "interest_coverage": 5.00,
    "revenue_growth_qoq": 0.05,
    "gross_margin_trend": 0.01,
    "financial_data_staleness_days": 30,
    "financial_data_is_stale": False,
    "is_public_company": False,
    # News — no negative signals
    "news_sentiment_7d": 0.10,
    "news_sentiment_30d": 0.20,
    "news_negative_count_30d": 0,
    "news_negative_velocity": 0.50,
    "news_credibility_weighted_score": 0.30,
    "topic_layoff_30d": False,
    "topic_bankruptcy_30d": False,
    "topic_strike_30d": False,
    "topic_disaster_30d": False,
    "topic_regulatory_30d": False,
    "news_article_count_30d": 5,
    # Shipping — normal operations
    "port_call_count_30d": 10,
    "port_call_count_90d": 30,
    "shipping_volume_delta_30d": 0.05,
    "shipping_volume_z_score": 0.20,
    "avg_port_dwell_time_7d": 24.0,
    "dwell_time_delta": 5.0,
    "shipping_anomaly_flag": False,
    "port_mapping_confidence": 0.95,
    # Geo — low risk, not sanctioned
    "country_risk_score": 25.0,
    "country_risk_trend_90d": 2.0,
    "on_sanctions_list": False,
    "parent_on_sanctions_list": False,
    "country_under_sanctions": False,
    "single_country_exposure": False,
    # Macro — expansion
    "commodity_price_delta_30d": 0.05,
    "energy_price_index_30d": 0.02,
    "high_yield_spread_delta_30d": 0.10,
    "industry_pmi": 55.0,
    # Metadata
    "data_completeness": 1.0,
    "feature_vector_created_at": _SCORED_AT,
}


def _make_fv(**overrides: object) -> SupplierFeatureVector:
    """Build a SupplierFeatureVector from _FV_DEFAULTS, applying any overrides."""
    return SupplierFeatureVector.model_validate({**_FV_DEFAULTS, **overrides})


def _make_record(
    supplier_id: str = "sup_test_001",
    feature_date: date = _FEATURE_DATE,
    score: int = 45,
    risk_level: str = "medium",
) -> DailyScoreRecord:
    """Build a DailyScoreRecord with sensible defaults for repository tests."""
    return DailyScoreRecord(
        supplier_id=supplier_id,
        score=score,
        risk_level=risk_level,
        signal_breakdown={"score": score, "risk_level": risk_level},
        model_version="heuristic_v0",
        feature_date=feature_date,
        scored_at=_SCORED_AT,
    )


# ── Scorer tests ──────────────────────────────────────────────────────────────


def test_high_risk_supplier() -> None:
    """Multiple adverse signals across categories should produce score >= 70."""
    fv = _make_fv(
        altman_z_score=0.8,           # distress zone → financial +30
        going_concern_flag=True,       # → financial +25
        news_negative_count_30d=6,     # → news +25
        news_sentiment_30d=-0.7,       # → news +20
        topic_bankruptcy_30d=True,     # → news +30
        shipping_volume_delta_30d=-0.6,  # → shipping +30
        country_risk_score=80.0,       # → geo +25
    )
    result = _SCORER.score(fv)
    assert result.score >= 70
    assert result.risk_level == "high"
    assert len(result.top_drivers) == 5
    assert result.model_version == "heuristic_v0"


def test_low_risk_supplier() -> None:
    """All-safe signals should produce score < 40 (low risk)."""
    result = _SCORER.score(_make_fv())  # all defaults = no risk signals
    assert result.score < 40
    assert result.risk_level == "low"


def test_missing_financial_data() -> None:
    """altman_z_score = None must apply uncertainty penalty of +10, not +0."""
    fv = _make_fv(altman_z_score=None)
    result = _SCORER.score(fv)
    z_contrib = next(d for d in result.all_signals if d.signal_name == "altman_z_score")
    assert z_contrib.contribution == 10.0
    assert z_contrib.direction == "increases_risk"
    assert z_contrib.raw_value is None


def test_sanctions_hit_dominates() -> None:
    """on_sanctions_list=True should produce geo_score >= 50 and drive high total."""
    fv = _make_fv(
        on_sanctions_list=True,        # geo +50
        country_risk_score=85.0,       # geo +25 (total geo = 75)
        altman_z_score=0.8,            # financial +30
        going_concern_flag=True,       # financial +25
        news_negative_count_30d=6,     # news +25
        news_sentiment_30d=-0.7,       # news +20
        topic_bankruptcy_30d=True,     # news +30
        shipping_volume_delta_30d=-0.6,  # shipping +30
    )
    result = _SCORER.score(fv)
    assert result.score >= 70
    assert result.risk_level == "high"
    assert result.geo_score >= 50.0
    sanctions_sig = next(d for d in result.all_signals if d.signal_name == "on_sanctions_list")
    assert sanctions_sig.contribution == 50.0


def test_score_bounded() -> None:
    """Pathological inputs — every rule triggered simultaneously — must stay 0–100."""
    fv = _make_fv(
        altman_z_score=0.5,
        going_concern_flag=True,
        current_ratio=0.1,
        debt_to_equity=10.0,
        interest_coverage=0.1,
        financial_data_is_stale=True,
        news_negative_count_30d=100,
        news_sentiment_30d=-1.0,
        news_negative_velocity=4.0,
        topic_bankruptcy_30d=True,
        topic_layoff_30d=True,
        topic_strike_30d=True,
        topic_disaster_30d=True,
        topic_regulatory_30d=True,
        shipping_volume_delta_30d=-1.0,
        shipping_volume_z_score=-5.0,
        shipping_anomaly_flag=True,
        dwell_time_delta=100.0,
        on_sanctions_list=True,
        country_under_sanctions=True,
        country_risk_score=100.0,
        country_risk_trend_90d=50.0,
        industry_pmi=30.0,
        commodity_price_delta_30d=0.5,
        high_yield_spread_delta_30d=2.0,
    )
    result = _SCORER.score(fv)
    assert 0 <= result.score <= 100
    assert result.financial_score <= 100.0
    assert result.news_score <= 100.0
    assert result.shipping_score <= 100.0
    assert result.geo_score <= 100.0
    assert result.macro_score <= 100.0


def test_data_completeness() -> None:
    """Completeness = 0.0 when all nullable feature columns are None; 1.0 when full."""
    null_overrides: dict[str, object] = {col: None for col in _NULLABLE_FEATURE_COLUMNS}
    all_none_fv = _make_fv(**null_overrides, data_completeness=0.0)
    assert _SCORER._data_completeness(all_none_fv) == 0.0

    full_fv = _make_fv()  # all nullable fields have safe non-None values
    assert _SCORER._data_completeness(full_fv) == 1.0


def test_output_schema_matches_ml_spec() -> None:
    """RiskScoreOutput fields, top_drivers count, and all_signals sort order."""
    fv = _make_fv(
        altman_z_score=0.8,
        going_concern_flag=True,
        news_negative_count_30d=6,
        news_sentiment_30d=-0.7,
        topic_bankruptcy_30d=True,
    )
    result = _SCORER.score(fv)

    # Required fields exist and have correct types
    assert isinstance(result.supplier_id, str)
    assert isinstance(result.score, int)
    assert result.risk_level in ("low", "medium", "high")
    assert isinstance(result.data_completeness, float)
    assert result.feature_date == fv.feature_date

    # top_drivers: exactly 5 (we have >> 5 signals total)
    assert len(result.top_drivers) == 5

    # all_signals sorted descending by abs(contribution)
    magnitudes = [abs(s.contribution) for s in result.all_signals]
    assert magnitudes == sorted(magnitudes, reverse=True)

    # all_signals is a superset of top_drivers
    assert len(result.all_signals) > 5
    assert result.top_drivers == result.all_signals[:5]


def test_model_version_tag() -> None:
    """Every score must be tagged with the model version."""
    result = _SCORER.score(_make_fv())
    assert result.model_version == "heuristic_v0"


def test_risk_level_thresholds() -> None:
    """_risk_level must return correct labels at boundary scores."""
    assert _SCORER._risk_level(0) == "low"
    assert _SCORER._risk_level(39) == "low"
    assert _SCORER._risk_level(40) == "medium"
    assert _SCORER._risk_level(69) == "medium"
    assert _SCORER._risk_level(70) == "high"
    assert _SCORER._risk_level(100) == "high"


def test_financial_grey_zone_contributes_less_than_distress() -> None:
    """Grey zone Z-score (+15) must contribute less than distress zone (+30)."""
    grey = _make_fv(altman_z_score=2.0)   # 1.23 < 2.0 < 2.90 → grey zone
    distress = _make_fv(altman_z_score=0.8)  # < 1.23 → distress zone

    grey_result = _SCORER.score(grey)
    distress_result = _SCORER.score(distress)

    grey_z = next(d for d in grey_result.all_signals if d.signal_name == "altman_z_score")
    distress_z = next(d for d in distress_result.all_signals if d.signal_name == "altman_z_score")

    assert grey_z.contribution == 15.0
    assert distress_z.contribution == 30.0
    assert distress_result.score > grey_result.score


def test_no_shipping_data_applies_uncertainty_penalty() -> None:
    """Shipping score should be 10 (uncertainty) when no port data is available."""
    fv = _make_fv(
        port_call_count_30d=None,
        shipping_volume_delta_30d=None,
    )
    result = _SCORER.score(fv)
    assert result.shipping_score == 10.0


# ── DailyScoreRecord model tests ──────────────────────────────────────────────


def test_daily_score_record_from_score_output() -> None:
    """from_score_output factory must preserve all fields from RiskScoreOutput."""
    output = _SCORER.score(_make_fv(altman_z_score=0.8, going_concern_flag=True))
    record = DailyScoreRecord.from_score_output(output)

    assert record.supplier_id == output.supplier_id
    assert record.score == output.score
    assert record.risk_level == output.risk_level
    assert record.model_version == output.model_version
    assert record.feature_date == output.feature_date
    assert isinstance(record.signal_breakdown, dict)
    assert "score" in record.signal_breakdown
    assert record.id  # UUID auto-generated, non-empty


def test_daily_score_record_auto_id() -> None:
    """Each DailyScoreRecord should get a unique auto-generated UUID."""
    r1 = _make_record()
    r2 = _make_record()
    assert r1.id != r2.id


# ── InMemoryScoreRepository tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inmemory_repo_upsert_and_get_latest() -> None:
    """Upserted record should be retrievable via get_latest_score."""
    repo = InMemoryScoreRepository()
    record = _make_record(score=55)
    await repo.upsert_daily_score(record)
    latest = await repo.get_latest_score(record.supplier_id)
    assert latest is not None
    assert latest.supplier_id == record.supplier_id
    assert latest.score == 55


@pytest.mark.asyncio
async def test_inmemory_repo_upsert_overwrites_same_date() -> None:
    """Second upsert for same (supplier, date) should overwrite the first."""
    repo = InMemoryScoreRepository()
    r1 = _make_record(score=50)
    r2 = _make_record(score=75)  # same supplier_id and feature_date
    await repo.upsert_daily_score(r1)
    await repo.upsert_daily_score(r2)
    latest = await repo.get_latest_score(r1.supplier_id)
    assert latest is not None
    assert latest.score == 75


@pytest.mark.asyncio
async def test_inmemory_repo_get_latest_returns_none_for_unknown() -> None:
    """get_latest_score should return None for a supplier with no scores."""
    repo = InMemoryScoreRepository()
    result = await repo.get_latest_score("nonexistent_supplier")
    assert result is None


@pytest.mark.asyncio
async def test_inmemory_repo_get_history_newest_first() -> None:
    """get_score_history should return records sorted newest-first."""
    repo = InMemoryScoreRepository()
    r1 = _make_record(feature_date=date(2026, 1, 1), score=40)
    r2 = _make_record(feature_date=date(2026, 2, 1), score=60)
    r3 = _make_record(feature_date=date(2026, 3, 1), score=80)
    for r in [r1, r2, r3]:
        await repo.upsert_daily_score(r)

    history = await repo.get_score_history("sup_test_001", days=365)
    assert len(history) == 3
    assert history[0].feature_date > history[1].feature_date > history[2].feature_date


@pytest.mark.asyncio
async def test_inmemory_repo_get_history_filters_by_days() -> None:
    """Records older than `days` cutoff should be excluded from history."""
    repo = InMemoryScoreRepository()
    old = _make_record(feature_date=date(2024, 1, 1), score=50)    # > 365 days ago
    recent = _make_record(feature_date=date.today(), score=60)
    await repo.upsert_daily_score(old)
    await repo.upsert_daily_score(recent)

    history = await repo.get_score_history("sup_test_001", days=90)
    assert len(history) == 1
    assert history[0].score == 60


@pytest.mark.asyncio
async def test_inmemory_repo_all_scores_convenience() -> None:
    """all_scores() should return all stored records regardless of supplier."""
    repo = InMemoryScoreRepository()
    await repo.upsert_daily_score(_make_record(supplier_id="sup_a", score=30))
    await repo.upsert_daily_score(_make_record(supplier_id="sup_b", score=70))
    assert len(repo.all_scores()) == 2
