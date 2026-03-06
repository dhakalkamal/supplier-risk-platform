-- Rolling financial features per supplier, derived from SEC EDGAR filings.
-- Joined to the supplier registry via CIK to get the canonical supplier_id.
--
-- Source:      stg_sec_financials, dim_suppliers
-- Grain:       one row per (supplier_id, period_end)
-- Note:        suppliers without a CIK (private, non-SEC filers) will have
--              NULL for all financial features in the feature vector. That is
--              correct — missing data is not zero.

with financials as (
    select * from {{ ref('stg_sec_financials') }}
),

suppliers as (
    select * from {{ ref('dim_suppliers') }}
),

features as (
    select
        -- Identifiers
        s.supplier_id,
        f.cik,
        f.period_end,
        f.filing_type,

        -- Raw financials (passed through for transparency)
        f.total_assets,
        f.current_assets,
        f.total_liabilities,
        f.current_liabilities,
        f.shareholders_equity,
        f.revenue,
        f.ebit,
        f.cash,
        f.long_term_debt,
        f.inventory,
        f.interest_expense,

        -- Altman Z' Score (already computed by SEC parser)
        f.altman_z_score,
        f.going_concern_flag,

        -- Liquidity ratios
        {{ div0('f.current_assets', 'f.current_liabilities') }}
                                                        as current_ratio,
        {{ div0('f.current_assets - f.inventory', 'f.current_liabilities') }}
                                                        as quick_ratio,
        {{ div0('f.cash', 'f.current_liabilities') }}
                                                        as cash_ratio,

        -- Leverage ratios
        {{ div0('f.long_term_debt', 'f.shareholders_equity') }}
                                                        as debt_to_equity,
        {{ div0('f.ebit', 'f.interest_expense') }}
                                                        as interest_coverage,

        -- Quarter-over-quarter revenue growth (requires at least 2 periods of data)
        {{ div0(
            'f.revenue - lag(f.revenue) over (partition by f.cik order by f.period_end)',
            'lag(f.revenue) over (partition by f.cik order by f.period_end)'
        ) }}                                            as revenue_growth_qoq,

        -- Data staleness
        (CURRENT_DATE - f.period_end)::int              as financial_data_staleness_days,
        (CURRENT_DATE - f.period_end)::int > 180        as financial_data_is_stale,

        -- Provenance
        f.ingested_at

    from financials f
    -- LEFT JOIN: suppliers without a CIK produce NULL supplier_id and are excluded below
    left join suppliers s on f.cik = s.cik
)

select * from features
-- Only keep rows we can tie to a known supplier
where supplier_id is not null
