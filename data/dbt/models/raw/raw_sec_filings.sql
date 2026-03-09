/*
  raw_sec_filings
  ---------------
  Points dbt at the raw.sec_filings table written by the SEC Kafka consumer.
  No transformations — immutable copy.
  Staging model stg_sec_financials reads from this.
*/

{{ config(
    materialized = 'view',
    schema       = 'raw'
) }}

select
    cik,
    company_name,
    filing_type,
    filed_date,
    period_of_report,

    -- Financial columns (NULL if not reported)
    total_assets,
    current_assets,
    total_liabilities,
    current_liabilities,
    retained_earnings,
    shareholders_equity,
    revenue,
    ebit,
    net_income,
    interest_expense,
    cash,
    long_term_debt,
    inventory,

    -- Derived fields computed at ingestion
    altman_z_score,
    going_concern_flag,

    source_url,
    ingested_at

from {{ source('raw', 'sec_filings') }}
