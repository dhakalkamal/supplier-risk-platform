-- Cleans and types raw SEC EDGAR filing data.
-- One row per company per reporting period (deduplicated).
--
-- Source:      raw.sec_filings (written by SEC EDGAR Kafka consumer)
-- Grain:       one row per (cik, period_end)
-- Dedup:       keep most recently ingested record per (cik, period_of_report)
-- Idempotent:  yes — running twice produces the same result

with source as (
    select * from {{ source('raw', 'sec_filings') }}
),

cleaned as (
    select
        -- Identity
        cik::varchar                                    as cik,
        company_name::varchar                           as company_name,
        filing_type::varchar                            as filing_type,
        filed_date::date                                as filed_date,
        period_of_report::date                          as period_end,

        -- Balance sheet (all nullable — missing data is not zero)
        total_assets::float                             as total_assets,
        current_assets::float                           as current_assets,
        total_liabilities::float                        as total_liabilities,
        current_liabilities::float                      as current_liabilities,
        retained_earnings::float                        as retained_earnings,
        shareholders_equity::float                      as shareholders_equity,
        inventory::float                                as inventory,
        cash::float                                     as cash,
        long_term_debt::float                           as long_term_debt,

        -- Income statement
        revenue::float                                  as revenue,
        ebit::float                                     as ebit,
        net_income::float                               as net_income,
        interest_expense::float                         as interest_expense,

        -- Derived (computed by SEC parser)
        altman_z_score::float                           as altman_z_score,
        going_concern_flag::boolean                     as going_concern_flag,

        -- Provenance
        source_url::varchar                             as source_url,
        ingested_at::timestamp                          as ingested_at,
        CURRENT_TIMESTAMP                               as _dbt_ingested_at,

        -- Surrogate key for unique testing
        cik::varchar || '_' || period_of_report::text   as filing_key,

        -- Dedup rank — 1 = most recently ingested per (cik, period)
        row_number() over (
            partition by cik, period_of_report
            order by ingested_at desc
        )                                               as _row_num

    from source
    where cik is not null
      and period_of_report is not null
      and total_assets > 0    -- basic sanity: zero/negative total assets is a data error
)

select
    cik,
    company_name,
    filing_type,
    filed_date,
    period_end,
    total_assets,
    current_assets,
    total_liabilities,
    current_liabilities,
    retained_earnings,
    shareholders_equity,
    inventory,
    cash,
    long_term_debt,
    revenue,
    ebit,
    net_income,
    interest_expense,
    altman_z_score,
    going_concern_flag,
    source_url,
    ingested_at,
    _dbt_ingested_at,
    filing_key
from cleaned
where _row_num = 1
