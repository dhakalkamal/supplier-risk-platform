-- Macroeconomic features per supplier, matched by industry code.
-- Pulls commodity prices, manufacturing PMI, and credit spread signals.
--
-- Source:      stg_macro_indicators, dim_suppliers
-- Grain:       one row per supplier_id (as-of today)
-- Note:        economy-wide series (industry_code IS NULL) apply to all suppliers.
--              Industry-specific series apply only to suppliers in that NAICS code.
--              NULL values mean data is not available — never substitute 0.

with macro as (
    select * from {{ ref('stg_macro_indicators') }}
),

suppliers as (
    select supplier_id, industry_code
    from {{ ref('dim_suppliers') }}
),

-- Commodity price delta: 30-day % change for industry-relevant commodity series
commodity_30d as (
    select
        industry_code,
        series_id,
        series_name,
        value                                           as current_value,
        lag(value, 30) over (
            partition by series_id
            order by series_date
        )                                               as value_30d_ago
    from macro
    where series_id like 'COMMODITY:%'
      and series_date = (
          select max(series_date) from macro m2
          where m2.series_id = macro.series_id
      )
),

commodity_delta as (
    select
        industry_code,
        -- Average delta across all commodity series for this industry
        avg({{ div0('current_value - value_30d_ago', 'value_30d_ago') }})
                                                        as commodity_price_delta_30d
    from commodity_30d
    where current_value is not null
      and value_30d_ago is not null
    group by industry_code
),

-- PMI: most recent manufacturing PMI for this industry
pmi_latest as (
    select distinct on (industry_code)
        coalesce(industry_code, 'ALL')                  as industry_code,
        value                                           as industry_pmi
    from macro
    where series_id like 'PMI:%'
      and value is not null
    order by industry_code, series_date desc
),

-- High-yield credit spread: 30-day delta (economy-wide — applies to all suppliers)
hy_spread as (
    select
        value                                           as hy_spread_current,
        lag(value, 30) over (order by series_date)     as hy_spread_30d_ago
    from macro
    where series_id = 'FRED:BAMLH0A0HYM2'
      and value is not null
    order by series_date desc
    limit 1
),

features as (
    select
        s.supplier_id,

        -- Commodity price delta for this supplier's industry
        cd.commodity_price_delta_30d,

        -- Manufacturing PMI for this industry (or economy-wide if no industry match)
        coalesce(
            pmi_ind.industry_pmi,
            pmi_all.industry_pmi
        )                                               as industry_pmi,

        -- High-yield spread delta (credit market stress indicator)
        {{ div0(
            'hy.hy_spread_current - hy.hy_spread_30d_ago',
            'hy.hy_spread_30d_ago'
        ) }}                                            as high_yield_spread_delta_30d

    from suppliers s
    -- Industry-specific commodity delta
    left join commodity_delta cd        on s.industry_code = cd.industry_code
    -- Industry-specific PMI
    left join pmi_latest pmi_ind        on s.industry_code = pmi_ind.industry_code
    -- Economy-wide PMI fallback
    left join pmi_latest pmi_all        on pmi_all.industry_code = 'ALL'
    -- Economy-wide HY spread (single row, cross-joined)
    cross join hy_spread hy
)

select * from features
