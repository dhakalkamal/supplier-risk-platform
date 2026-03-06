-- Aggregates raw AIS vessel tracking events to vessel/port/day grain.
-- One row per (mmsi, port_id, event_date, event_type).
--
-- Source:      raw.ais_events (written by AIS Kafka consumer)
-- Grain:       one row per (mmsi, port_id, event_date, event_type)
-- Dedup:       row_number() over (mmsi, port_id, event_date, event_type) keeps latest
-- Idempotent:  yes — running twice produces the same result

with source as (
    select * from {{ source('raw', 'ais_events') }}
),

cleaned as (
    select
        -- Identity
        event_id::varchar                               as event_id,
        mmsi::varchar                                   as mmsi,
        vessel_name::varchar                            as vessel_name,
        imo_number::varchar                             as imo_number,
        supplier_id::varchar                            as supplier_id,

        -- Location
        port_id::varchar                                as port_id,
        port_name::varchar                              as port_name,
        country_code::varchar                           as country_code,

        -- Event
        event_type::varchar                             as event_type,
        event_timestamp::timestamp                      as event_timestamp,
        event_timestamp::date                           as event_date,
        cargo_type::varchar                             as cargo_type,
        gross_tonnage::float                            as gross_tonnage,

        -- Dwell time is only meaningful for arrival events
        case when event_type = 'arrival' then dwell_hours::float else null end
                                                        as dwell_hours,

        -- Provenance
        ingested_at::timestamp                          as ingested_at,
        CURRENT_TIMESTAMP                               as _dbt_ingested_at,

        -- Surrogate key for dedup
        mmsi::varchar || '_' || port_id::varchar
            || '_' || event_timestamp::date::text
            || '_' || event_type::varchar               as ais_event_key,

        row_number() over (
            partition by mmsi, port_id, event_timestamp::date, event_type
            order by ingested_at desc
        )                                               as _row_num

    from source
    where event_id is not null
      and mmsi is not null
      and port_id is not null
      and event_timestamp is not null
      and event_type in ('arrival', 'departure')
)

select
    event_id,
    mmsi,
    vessel_name,
    imo_number,
    supplier_id,
    port_id,
    port_name,
    country_code,
    event_type,
    event_timestamp,
    event_date,
    cargo_type,
    gross_tonnage,
    dwell_hours,
    ingested_at,
    _dbt_ingested_at,
    ais_event_key
from cleaned
where _row_num = 1
