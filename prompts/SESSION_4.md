# SESSION_4.md — dbt Models: Raw → Staging → Marts

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md, then read prompts/SESSION_4.md and follow it exactly.
```

Only start after Session 3 checklist is fully green.

---

## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `docs/ARCHITECTURE.md` — "Snowflake Schema" section

Confirm:
> "I am building dbt models under data/dbt/. The flow is raw → staging → marts. The final mart is marts.supplier_feature_vector which joins all signal sources and feeds the ML pipeline."

---

## RULES FOR THIS SESSION

- Every model must have a corresponding schema.yml entry with description, column descriptions, and tests.
- dbt tests on every model: at minimum `not_null` and `unique` on primary keys.
- No raw SQL outside of dbt models. No Python scripts that transform data in Snowflake.
- Run `dbt compile` to check SQL syntax after each model group.
- Staging models must be idempotent — running them twice produces the same result.

---

## STEP 1: dbt Project Setup

### `data/dbt/dbt_project.yml`
```yaml
name: supplier_risk
version: '1.0.0'
config-version: 2

profile: supplier_risk

model-paths: ["models"]
test-paths: ["tests"]
seed-paths: ["seeds"]

models:
  supplier_risk:
    raw:
      +schema: raw
      +materialized: view        # raw models are views over source tables
    staging:
      +schema: staging
      +materialized: table
      +on_schema_change: fail    # fail loudly if schema changes
    marts:
      +schema: marts
      +materialized: table
```

### `data/dbt/profiles.yml.example`
```yaml
supplier_risk:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      password: "{{ env_var('SNOWFLAKE_PASSWORD') }}"
      role: TRANSFORMER
      database: SUPPLIER_RISK_DEV
      warehouse: COMPUTE_WH
      schema: dbt_dev
      threads: 4
    prod:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      password: "{{ env_var('SNOWFLAKE_PASSWORD') }}"
      role: TRANSFORMER
      database: SUPPLIER_RISK_PROD
      warehouse: COMPUTE_WH
      schema: public
      threads: 8
```

### `data/dbt/models/sources.yml`
Define all raw source tables that dbt reads from (these are written by the ingestion pipelines):
```yaml
sources:
  - name: raw
    database: SUPPLIER_RISK
    schema: raw
    tables:
      - name: sec_filings
      - name: news_articles
      - name: ais_events
      - name: macro_series
      - name: geo_events
```

---

## STEP 2: Staging Models

Build one staging model per source. Each staging model:
- Selects from the raw source
- Renames columns to snake_case
- Casts types explicitly (never trust source types)
- Filters out clearly bad rows (nulls on required fields)
- Adds `_dbt_ingested_at` timestamp
- Is idempotent

### `models/staging/stg_sec_financials.sql`
```sql
-- Cleans and types the raw SEC filing data
-- One row per company per reporting period
-- Deduplicates: keep most recently ingested record per (cik, period_end)

with source as (
    select * from {{ source('raw', 'sec_filings') }}
),

cleaned as (
    select
        cik::varchar                               as cik,
        company_name::varchar                      as company_name,
        filing_type::varchar                       as filing_type,
        filed_date::date                           as filed_date,
        period_of_report::date                     as period_end,
        total_assets::float                        as total_assets,
        current_assets::float                      as current_assets,
        total_liabilities::float                   as total_liabilities,
        current_liabilities::float                 as current_liabilities,
        retained_earnings::float                   as retained_earnings,
        shareholders_equity::float                 as shareholders_equity,
        revenue::float                             as revenue,
        ebit::float                                as ebit,
        cash::float                                as cash,
        long_term_debt::float                      as long_term_debt,
        interest_expense::float                    as interest_expense,
        inventory::float                           as inventory,
        altman_z_score::float                      as altman_z_score,
        going_concern_flag::boolean                as going_concern_flag,
        ingested_at::timestamp_ntz                 as ingested_at,
        current_timestamp()                        as _dbt_ingested_at,
        row_number() over (
            partition by cik, period_of_report
            order by ingested_at desc
        )                                          as _row_num
    from source
    where cik is not null
      and period_of_report is not null
      and total_assets > 0          -- basic sanity check
)

select * from cleaned where _row_num = 1
```

Build equivalent staging models for all 5 sources:
- `stg_sec_financials.sql` — above
- `stg_news_sentiment.sql` — from raw.news_articles, one row per article
- `stg_shipping_volume.sql` — from raw.ais_events, aggregated to port/day
- `stg_geo_risk.sql` — from raw.geo_events, country risk scores by date
- `stg_macro_indicators.sql` — from raw.macro_series, one row per series/date

### schema.yml for staging
Every model needs:
```yaml
models:
  - name: stg_sec_financials
    description: "Cleaned SEC EDGAR financial data. One row per company per reporting period."
    columns:
      - name: cik
        description: "SEC Central Index Key, zero-padded to 10 digits"
        tests:
          - not_null
      - name: period_end
        description: "End date of the reporting period"
        tests:
          - not_null
      - name: altman_z_score
        description: "Altman Z-Score computed from filing data. NULL if data insufficient."
      # ... all columns documented
    tests:
      - unique:
          column_name: "cik || '_' || period_end"  # composite unique
```

---

## STEP 3: Feature Engineering Mart

### `models/marts/supplier_financial_features.sql`
```sql
-- Rolling financial features per supplier, computed over last 4 quarters
-- Joins SEC data to supplier registry to get supplier_id

with financials as (
    select * from {{ ref('stg_sec_financials') }}
),

supplier_registry as (
    select * from {{ ref('dim_suppliers') }}
),

-- Join and compute rolling features
features as (
    select
        sr.supplier_id,
        f.cik,
        f.period_end,
        f.altman_z_score,
        f.going_concern_flag,
        f.total_assets,
        f.current_assets,
        f.total_liabilities,
        f.current_liabilities,

        -- Liquidity ratios
        div0(f.current_assets, f.current_liabilities)          as current_ratio,
        div0(f.current_assets - f.inventory, f.current_liabilities) as quick_ratio,
        div0(f.cash, f.current_liabilities)                    as cash_ratio,

        -- Leverage
        div0(f.long_term_debt, f.shareholders_equity)          as debt_to_equity,
        div0(f.ebit, f.interest_expense)                       as interest_coverage,

        -- Quarter-over-quarter trends (using lag)
        div0(f.revenue - lag(f.revenue) over (
            partition by f.cik order by f.period_end
        ), lag(f.revenue) over (
            partition by f.cik order by f.period_end
        ))                                                     as revenue_growth_qoq,

        -- Staleness flag
        datediff('day', f.period_end, current_date())         as financial_data_staleness_days,
        datediff('day', f.period_end, current_date()) > 180   as is_stale,

        f.ingested_at

    from financials f
    left join supplier_registry sr on f.cik = sr.cik
)

select * from features
```

Build equivalent mart models:
- `supplier_financial_features.sql` — above
- `supplier_news_features.sql` — rolling 7d/30d sentiment, topic counts per supplier
- `supplier_shipping_features.sql` — volume delta, dwell time delta per supplier
- `supplier_geo_features.sql` — country risk score, sanctions flags per supplier
- `supplier_macro_features.sql` — commodity price delta, PMI per supplier industry

### `models/marts/supplier_feature_vector.sql`
The crown jewel — joins all feature marts into a single wide table for ML:

```sql
-- Master feature vector for ML scoring
-- One row per supplier per date
-- This is the direct input to the ML pipeline

with financial    as (select * from {{ ref('supplier_financial_features') }}),
     news         as (select * from {{ ref('supplier_news_features') }}),
     shipping     as (select * from {{ ref('supplier_shipping_features') }}),
     geo          as (select * from {{ ref('supplier_geo_features') }}),
     macro        as (select * from {{ ref('supplier_macro_features') }})

select
    coalesce(f.supplier_id, n.supplier_id, s.supplier_id)  as supplier_id,
    current_date()                                          as feature_date,

    -- Financial features (30% weight in model)
    f.altman_z_score,
    f.going_concern_flag,
    f.current_ratio,
    f.quick_ratio,
    f.debt_to_equity,
    f.interest_coverage,
    f.revenue_growth_qoq,
    f.financial_data_staleness_days,
    f.is_stale                                             as financial_data_is_stale,

    -- News features (25% weight)
    n.sentiment_score_7d,
    n.sentiment_score_30d,
    n.negative_article_count_30d,
    n.negative_velocity,
    n.topic_layoff_flag_30d,
    n.topic_bankruptcy_flag_30d,
    n.topic_strike_flag_30d,
    n.topic_disaster_flag_30d,
    n.topic_regulatory_flag_30d,

    -- Shipping features (20% weight)
    s.port_call_count_30d,
    s.shipping_volume_delta_30d,
    s.shipping_volume_z_score,
    s.avg_dwell_time_7d,
    s.dwell_time_delta,
    s.shipping_anomaly_flag,

    -- Geo features (15% weight)
    g.country_risk_score,
    g.country_risk_trend_90d,
    g.on_sanctions_list,
    g.country_under_sanctions,

    -- Macro features (10% weight)
    m.commodity_price_delta_30d,
    m.industry_pmi,
    m.high_yield_spread_delta_30d,

    current_timestamp()                                    as feature_vector_created_at

from financial f
full outer join news     n on f.supplier_id = n.supplier_id
full outer join shipping s on coalesce(f.supplier_id, n.supplier_id) = s.supplier_id
full outer join geo      g on coalesce(f.supplier_id, n.supplier_id, s.supplier_id) = g.supplier_id
full outer join macro    m on coalesce(f.supplier_id, n.supplier_id, s.supplier_id) = m.supplier_id
```

### `models/marts/dim_suppliers.sql`
Supplier dimension table — canonical supplier registry:
```sql
-- Dimension table: one row per canonical supplier
-- Source of truth for supplier metadata
select
    supplier_id,
    canonical_name,
    country,
    industry_code,
    duns_number,
    created_at,
    updated_at
from {{ source('raw', 'supplier_registry') }}
```

---

## STEP 4: dbt Tests

Add these custom dbt tests in `data/dbt/tests/`:

### `test_altman_z_score_range.sql`
```sql
-- Z-scores should be between -10 and 20. Extreme outliers = data error.
select cik, period_end, altman_z_score
from {{ ref('stg_sec_financials') }}
where altman_z_score is not null
  and (altman_z_score < -10 or altman_z_score > 20)
```

### `test_sentiment_score_range.sql`
```sql
-- Sentiment must be between -1.0 and 1.0
select article_id, sentiment_score
from {{ ref('stg_news_sentiment') }}
where sentiment_score < -1.0 or sentiment_score > 1.0
```

### `test_feature_vector_no_orphans.sql`
```sql
-- Every row in supplier_feature_vector must have a valid supplier_id
select fv.supplier_id
from {{ ref('supplier_feature_vector') }} fv
left join {{ ref('dim_suppliers') }} d on fv.supplier_id = d.supplier_id
where d.supplier_id is null
```

---

## SESSION 4 DONE — CHECKLIST

```
□ dbt compile runs with zero errors
□ All 5 staging models built and documented in schema.yml
□ All 5 feature mart models built
□ supplier_feature_vector joins all 5 feature marts correctly
□ dim_suppliers model exists
□ Every model has not_null + unique tests on primary key columns
□ All columns in all models documented in schema.yml
□ 3 custom SQL tests written
□ div0() used instead of division — no divide-by-zero errors
□ Staging models deduplicate using row_number() window function
□ profiles.yml.example uses env vars, no hardcoded credentials
```

**Say: "Session 4 complete. Checklist: X/11 items green."**

Next: `prompts/SESSION_5.md`
