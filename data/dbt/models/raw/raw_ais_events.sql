/*
  raw_ais_events
  --------------
  Points dbt at the raw.ais_events table written by the AIS Kafka consumer.
  No transformations — immutable copy.
  Staging model stg_shipping_volume reads from this.
*/

{{ config(
    materialized = 'view',
    schema       = 'raw'
) }}

select
    event_id,
    mmsi,                     -- 9-digit Maritime Mobile Service Identity
    vessel_name,
    imo_number,               -- stable vessel ID across ownership changes
    port_id,                  -- UN/LOCODE of the port
    port_name,
    country_code,             -- ISO 3166-1 alpha-2 of the port
    event_type,               -- "arrival" or "departure"
    event_timestamp,
    cargo_type,               -- NULL if not broadcast
    gross_tonnage,            -- NULL if not broadcast
    dwell_hours,              -- NULL for departure events
    supplier_id,              -- resolved supplier, NULL if not resolved
    ingested_at

from {{ source('raw', 'ais_events') }}
