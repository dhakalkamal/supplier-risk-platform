-- Cleans and types raw geopolitical risk events.
-- One row per event. Deduplicates by event_id (latest ingested wins).
--
-- Source:      raw.geo_events (written by geo Kafka consumer)
-- Grain:       one row per event_id
-- Dedup:       row_number() over (event_id) keeps the most recently ingested copy
-- Idempotent:  yes — running twice produces the same result

with source as (
    select * from {{ source('raw', 'geo_events') }}
),

cleaned as (
    select
        -- Identity
        event_id::varchar                               as event_id,
        country_code::varchar                           as country_code,
        event_type::varchar                             as event_type,
        event_date::date                                as event_date,

        -- Risk signals
        severity_score::float                           as severity_score,
        on_sanctions_list::boolean                      as on_sanctions_list,
        description::varchar                            as description,
        source_name::varchar                            as source_name,

        -- Provenance
        ingested_at::timestamp                          as ingested_at,
        CURRENT_TIMESTAMP                               as _dbt_ingested_at,

        row_number() over (
            partition by event_id
            order by ingested_at desc
        )                                               as _row_num

    from source
    where event_id is not null
      and country_code is not null
      and event_date is not null
      -- Reject events with out-of-range severity — these are data errors
      and severity_score between 0.0 and 1.0
)

select
    event_id,
    country_code,
    event_type,
    event_date,
    severity_score,
    on_sanctions_list,
    description,
    source_name,
    ingested_at,
    _dbt_ingested_at
from cleaned
where _row_num = 1
