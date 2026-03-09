/*
  raw_geo_events
  --------------
  Points dbt at the raw.geo_events table written by the Geo Kafka consumer.
  Sources: ACLED conflict data, GDELT, OFAC sanctions lists.
  No transformations — immutable copy.
  Staging model stg_geo_risk reads from this.
*/

{{ config(
    materialized = 'view',
    schema       = 'raw'
) }}

select
    event_id,
    country_code,             -- ISO 3166-1 alpha-2
    event_type,               -- "conflict", "sanction", "natural_disaster", "political_instability"
    event_date,
    severity_score,           -- 0.0–1.0, higher = more severe
    on_sanctions_list,        -- TRUE if country is on OFAC/EU/UN sanctions list
    description,
    source_name,              -- "ACLED", "GDELT", "OFAC"
    ingested_at

from {{ source('raw', 'geo_events') }}
