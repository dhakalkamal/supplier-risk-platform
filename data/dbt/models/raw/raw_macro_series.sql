/*
  raw_macro_series
  ----------------
  Points dbt at the raw.macro_series table written by the Macro Kafka consumer.
  Sources: FRED, World Bank, commodity APIs.
  No transformations — immutable copy.
  Staging model stg_macro_indicators reads from this.
*/

{{ config(
    materialized = 'view',
    schema       = 'raw'
) }}

select
    series_id,                -- e.g. "FRED:T10Y2Y", "WB:PMI_MFG_US"
    series_name,
    series_date,
    value,                    -- NULL if data not available for this date
    unit,                     -- e.g. "percent", "index", "USD/barrel"
    industry_code,            -- NAICS code, NULL for economy-wide series
    source_name,              -- "FRED", "WorldBank", "EIA"
    ingested_at

from {{ source('raw', 'macro_series') }}
