-- Override dbt's default schema naming.
--
-- Default dbt behaviour prefixes the custom schema with target.schema, producing
-- names like "pipeline_staging". We override so each layer gets a clean schema:
--   raw models     → raw
--   staging models → staging   (distinct from ingestion pipeline's staging.* tables
--                               because dbt writes to dbt-managed schemas via this project)
--   marts models   → marts
--
-- In dev, set target.schema = dbt_dev in profiles.yml to get dbt_dev_staging etc.
-- In prod, set target.schema = dbt_prod.

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ default_schema }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
