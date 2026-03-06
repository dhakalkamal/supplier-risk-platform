"""SupplierFeatureVector — single source of truth for ML feature schema.

The dbt mart `marts.supplier_feature_vector` must produce column names that match
these field names exactly. Change a field name here → update dbt first.

See ML_SPEC.md Section 2 for full documentation.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


class SupplierFeatureVector(BaseModel):
    """Complete feature vector for one supplier on one date.

    None means the signal is genuinely missing — not zero, not unknown.
    Never substitute 0 for None — it biases every ratio that uses this feature.
    XGBoost handles None as np.nan natively (see ML_SPEC.md Section 4.2).
    """

    model_config = ConfigDict(frozen=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    supplier_id: str
    feature_date: date

    # ── Financial Features (30% weight) ───────────────────────────────────────
    # Source: staging.stg_sec_financials → marts.supplier_financial_features
    altman_z_score: float | None  # Z' score (private company formula)
    altman_working_capital_ratio: float | None  # working_capital / total_assets
    altman_retained_earnings_ratio: float | None  # retained_earnings / total_assets
    altman_ebit_ratio: float | None  # ebit / total_assets
    altman_equity_to_debt: float | None  # book_equity / total_liabilities
    altman_revenue_ratio: float | None  # revenue / total_assets
    going_concern_flag: bool | None  # True if 10-K flags going concern
    current_ratio: float | None  # current_assets / current_liabilities
    quick_ratio: float | None  # (current_assets - inventory) / current_liabilities
    cash_ratio: float | None  # cash / current_liabilities
    debt_to_equity: float | None  # total_debt / shareholders_equity
    interest_coverage: float | None  # ebit / interest_expense
    revenue_growth_qoq: float | None  # (revenue_q - revenue_q1) / revenue_q1
    gross_margin_trend: float | None  # gross_margin_q - gross_margin_q4
    financial_data_staleness_days: int | None  # days since last filing
    financial_data_is_stale: bool  # True if staleness_days > 180
    is_public_company: bool  # False = private, financial features likely None

    # ── News Sentiment Features (25% weight) ──────────────────────────────────
    # Source: staging.stg_news_sentiment → marts.supplier_news_features
    news_sentiment_7d: float | None  # mean sentiment last 7d (-1 to +1)
    news_sentiment_30d: float | None  # mean sentiment last 30d
    news_negative_count_30d: int | None  # articles with sentiment < -0.3
    news_negative_velocity: float | None  # negative_7d / negative_30d ratio
    news_credibility_weighted_score: float | None  # sentiment weighted by source credibility
    topic_layoff_30d: bool  # layoff-related news in last 30d
    topic_bankruptcy_30d: bool  # bankruptcy-related news in last 30d
    topic_strike_30d: bool  # strike/industrial action in last 30d
    topic_disaster_30d: bool  # fire/explosion/disaster in last 30d
    topic_regulatory_30d: bool  # regulatory action/fine in last 30d
    news_article_count_30d: int | None  # total articles (coverage check)

    # ── Shipping Volume Features (20% weight) ─────────────────────────────────
    # Source: staging.stg_shipping_volume → marts.supplier_shipping_features
    port_call_count_30d: int | None  # vessel calls at primary port, 30d
    port_call_count_90d: int | None  # vessel calls at primary port, 90d
    shipping_volume_delta_30d: float | None  # % change vs prior 30d
    shipping_volume_z_score: float | None  # z-score vs historical baseline
    avg_port_dwell_time_7d: float | None  # mean hours at berth, 7d
    dwell_time_delta: float | None  # avg_dwell_7d - avg_dwell_90d
    shipping_anomaly_flag: bool  # True if z_score < -2 or 50%+ drop
    port_mapping_confidence: float | None  # 0.0–1.0, confidence in port mapping

    # ── Geopolitical Risk Features (15% weight) ───────────────────────────────
    # Source: staging.stg_geo_risk → marts.supplier_geo_features
    country_risk_score: float | None  # composite 0–100, higher = more risk
    country_risk_trend_90d: float | None  # delta vs 90 days ago
    on_sanctions_list: bool  # direct OFAC SDN hit
    parent_on_sanctions_list: bool  # parent/subsidiary on list
    country_under_sanctions: bool  # broad country-level sanctions
    single_country_exposure: bool  # all primary ops in one country

    # ── Macro / Input Cost Features (10% weight) ──────────────────────────────
    # Source: staging.stg_macro_indicators → marts.supplier_macro_features
    commodity_price_delta_30d: float | None  # % change in primary input commodity
    energy_price_index_30d: float | None  # regional energy cost delta
    high_yield_spread_delta_30d: float | None  # HY spread change (credit stress proxy)
    industry_pmi: float | None  # PMI for supplier's industry

    # ── Data Quality Metadata ─────────────────────────────────────────────────
    data_completeness: float  # 0.0–1.0, fraction of non-None signals
    feature_vector_created_at: datetime

    @field_validator("altman_z_score")
    @classmethod
    def z_score_reasonable(cls, v: float | None) -> float | None:
        """Z-scores outside -10 to 20 indicate data error, not genuine distress."""
        if v is not None and not (-10 <= v <= 20):
            raise ValueError(f"Altman Z-Score {v} is outside plausible range [-10, 20]")
        return v

    @field_validator("data_completeness")
    @classmethod
    def completeness_bounded(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"data_completeness must be 0.0–1.0, got {v}")
        return v


# Canonical ordered list of features fed to XGBoost.
# Column order must match training exactly — never reorder without retraining.
FEATURE_COLUMNS: list[str] = [
    # Financial (30%)
    "altman_z_score",
    "altman_working_capital_ratio",
    "altman_retained_earnings_ratio",
    "altman_ebit_ratio",
    "altman_equity_to_debt",
    "altman_revenue_ratio",
    "going_concern_flag",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "debt_to_equity",
    "interest_coverage",
    "revenue_growth_qoq",
    "gross_margin_trend",
    "financial_data_is_stale",
    "is_public_company",
    # News (25%)
    "news_sentiment_7d",
    "news_sentiment_30d",
    "news_negative_count_30d",
    "news_negative_velocity",
    "news_credibility_weighted_score",
    "topic_layoff_30d",
    "topic_bankruptcy_30d",
    "topic_strike_30d",
    "topic_disaster_30d",
    "topic_regulatory_30d",
    "news_article_count_30d",
    # Shipping (20%)
    "port_call_count_30d",
    "port_call_count_90d",
    "shipping_volume_delta_30d",
    "shipping_volume_z_score",
    "avg_port_dwell_time_7d",
    "dwell_time_delta",
    "shipping_anomaly_flag",
    "port_mapping_confidence",
    # Geopolitical (15%)
    "country_risk_score",
    "country_risk_trend_90d",
    "on_sanctions_list",
    "parent_on_sanctions_list",
    "country_under_sanctions",
    "single_country_exposure",
    # Macro (10%)
    "commodity_price_delta_30d",
    "energy_price_index_30d",
    "high_yield_spread_delta_30d",
    "industry_pmi",
]
