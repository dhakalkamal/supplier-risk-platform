-- Cleans and types raw macroeconomic time-series data.
-- One row per (series_id, series_date). Latest ingested value wins on dedup.
--
-- Source:      raw.macro_series (written by macro Kafka consumer)
-- Grain:       one row per (series_id, series_date)
-- Dedup:       row_number() over (series_id, series_date) keeps most recently ingested
-- Idempotent:  yes — running twice produces the same result

with source as (
    select * from {{ source('raw', 'macro_series') }}
),

cleaned as (
    select
        -- Identity
        series_id::varchar                              as series_id,
        series_name::varchar                            as series_name,
        series_date::date                               as series_date,
        industry_code::varchar                          as industry_code,

        -- Value (NULL means data not available for this date — never substitute 0)
        value::float                                    as value,
        unit::varchar                                   as unit,
        source_name::varchar                            as source_name,

        -- Provenance
        ingested_at::timestamp                          as ingested_at,
        CURRENT_TIMESTAMP                               as _dbt_ingested_at,

        -- Surrogate key for unique testing
        series_id::varchar || '_' || series_date::text  as series_key,

        row_number() over (
            partition by series_id, series_date
            order by ingested_at desc
        )                                               as _row_num

    from source
    where series_id is not null
      and series_date is not null
)

select
    series_id,
    series_name,
    series_date,
    industry_code,
    value,
    unit,
    source_name,
    ingested_at,
    _dbt_ingested_at,
    series_key
from cleaned
where _row_num = 1
