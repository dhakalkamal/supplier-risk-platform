-- Geopolitical risk features per supplier, derived from country-level risk events.
-- Joins the supplier's primary country to the geo risk event table.
--
-- Source:      stg_geo_risk, dim_suppliers
-- Grain:       one row per supplier_id (as-of today)
-- Note:        country risk is assigned at the supplier's country level.
--              Suppliers operating across multiple countries get their primary
--              country's risk. Multi-country exposure is a Phase 2 enhancement.

with geo as (
    select * from {{ ref('stg_geo_risk') }}
),

suppliers as (
    select supplier_id, country
    from {{ ref('dim_suppliers') }}
    where country is not null
),

-- Latest risk score per country (most recent event within 90 days)
country_risk_current as (
    select
        country_code,
        -- Aggregate severity across all active event types
        avg(severity_score)                             as country_risk_score,
        bool_or(on_sanctions_list)                      as country_under_sanctions
    from geo
    where event_date >= CURRENT_DATE - interval '90 days'
    group by country_code
),

-- 90-day trend: compare recent 30d avg to prior 60d avg
country_risk_30d as (
    select country_code, avg(severity_score) as avg_severity_30d
    from geo
    where event_date >= CURRENT_DATE - interval '30 days'
    group by country_code
),

country_risk_prior_60d as (
    select country_code, avg(severity_score) as avg_severity_prior_60d
    from geo
    where event_date >= CURRENT_DATE - interval '90 days'
      and event_date < CURRENT_DATE - interval '30 days'
    group by country_code
),

-- Most recent sanctions flag per country (point-in-time)
country_sanctions as (
    select distinct on (country_code)
        country_code,
        on_sanctions_list
    from geo
    order by country_code, event_date desc
),

features as (
    select
        s.supplier_id,

        -- Country risk score (0.0–1.0, higher = more risky)
        coalesce(cr.country_risk_score, 0.0)            as country_risk_score,

        -- Trend: positive = risk increasing over last 90 days
        coalesce(r30.avg_severity_30d, 0.0)
            - coalesce(r60.avg_severity_prior_60d, 0.0) as country_risk_trend_90d,

        -- Sanctions flags
        coalesce(cs.on_sanctions_list, false)           as on_sanctions_list,
        coalesce(cr.country_under_sanctions, false)     as country_under_sanctions,

        -- Pass-through for transparency
        s.country                                       as supplier_country

    from suppliers s
    left join country_risk_current cr   on s.country = cr.country_code
    left join country_risk_30d r30      on s.country = r30.country_code
    left join country_risk_prior_60d r60 on s.country = r60.country_code
    left join country_sanctions cs      on s.country = cs.country_code
)

select * from features
