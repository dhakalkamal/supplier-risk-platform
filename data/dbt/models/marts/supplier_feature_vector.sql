-- Master feature vector for ML scoring.
-- One row per supplier (as-of today), joining all 5 signal categories.
--
-- This is the direct input to the ML pipeline (ml/features/feature_vector.py).
-- Column names here MUST match the field names in SupplierFeatureVector exactly.
-- If you rename a column here, update SupplierFeatureVector first. See CLAUDE.md §8.
--
-- Sources:     supplier_financial_features (30% model weight)
--              supplier_news_features      (25% model weight)
--              supplier_shipping_features  (20% model weight)
--              supplier_geo_features       (15% model weight)
--              supplier_macro_features     (10% model weight)
--
-- Grain:       one row per supplier_id
-- NULL policy: NULL = signal not available. Never substitute 0. XGBoost handles
--              np.nan natively — the Python loader must convert SQL NULL to np.nan.

with financial    as (select * from {{ ref('supplier_financial_features') }}),
     news         as (select * from {{ ref('supplier_news_features') }}),
     shipping     as (select * from {{ ref('supplier_shipping_features') }}),
     geo          as (select * from {{ ref('supplier_geo_features') }}),
     macro        as (select * from {{ ref('supplier_macro_features') }}),

-- All known suppliers from dim_suppliers anchors the join so every supplier
-- appears in the feature vector even if some signal sources have no data.
suppliers as (select * from {{ ref('dim_suppliers') }})

select
    s.supplier_id,
    CURRENT_DATE                                        as feature_date,

    -- ── Financial features (30% weight) ──────────────────────────────────────
    -- NULL when supplier has no SEC filing (private company or not yet resolved)
    f.altman_z_score,
    f.going_concern_flag,
    f.current_ratio,
    f.quick_ratio,
    f.debt_to_equity,
    f.interest_coverage,
    f.revenue_growth_qoq,
    f.financial_data_staleness_days,
    f.financial_data_is_stale,

    -- ── News features (25% weight) ───────────────────────────────────────────
    -- NULL when supplier has no articles in the window
    n.sentiment_score_7d,
    n.sentiment_score_30d,
    n.negative_article_count_30d,
    n.negative_velocity,
    n.topic_layoff_flag_30d,
    n.topic_bankruptcy_flag_30d,
    n.topic_strike_flag_30d,
    n.topic_disaster_flag_30d,
    n.topic_regulatory_flag_30d,

    -- ── Shipping features (20% weight) ───────────────────────────────────────
    -- NULL when supplier has no resolved AIS events in the window
    sh.port_call_count_30d,
    sh.shipping_volume_delta_30d,
    sh.shipping_volume_z_score,
    sh.avg_dwell_time_7d,
    sh.dwell_time_delta,
    sh.shipping_anomaly_flag,

    -- ── Geo features (15% weight) ────────────────────────────────────────────
    -- NULL when supplier country has no geo events (treated as no risk signal)
    g.country_risk_score,
    g.country_risk_trend_90d,
    g.on_sanctions_list,
    g.country_under_sanctions,

    -- ── Macro features (10% weight) ──────────────────────────────────────────
    -- NULL when no macro series available for this industry
    m.commodity_price_delta_30d,
    m.industry_pmi,
    m.high_yield_spread_delta_30d,

    -- ── Metadata ──────────────────────────────────────────────────────────────
    CURRENT_TIMESTAMP                                   as feature_vector_created_at

from suppliers s
left join financial  f   on s.supplier_id = f.supplier_id
left join news       n   on s.supplier_id = n.supplier_id
left join shipping   sh  on s.supplier_id = sh.supplier_id
left join geo        g   on s.supplier_id = g.supplier_id
left join macro      m   on s.supplier_id = m.supplier_id
