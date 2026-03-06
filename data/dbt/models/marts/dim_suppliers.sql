-- Supplier dimension table — canonical supplier registry.
-- Source of truth for supplier metadata used across all feature marts.
--
-- Source:      raw.supplier_registry (written by entity resolution pipeline)
-- Grain:       one row per supplier_id
-- Note:        aliases (array type) is excluded here — arrays do not join cleanly
--              in SQL. Alias lookup lives in the entity resolution service.

select
    supplier_id::varchar                            as supplier_id,
    canonical_name::varchar                         as canonical_name,
    country::varchar                                as country,
    industry_code::varchar                          as industry_code,
    duns_number::varchar                            as duns_number,
    website::varchar                                as website,
    cik::varchar                                    as cik,
    created_at::timestamp                           as created_at,
    updated_at::timestamp                           as updated_at,
    CURRENT_TIMESTAMP                               as _dbt_ingested_at

from {{ source('raw', 'supplier_registry') }}
where supplier_id is not null
  and canonical_name is not null
