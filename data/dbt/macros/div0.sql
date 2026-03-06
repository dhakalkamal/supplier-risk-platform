-- Postgres-compatible safe division macro.
--
-- Snowflake has a native div0(numerator, denominator) that returns NULL instead
-- of raising on divide-by-zero. Postgres does not. This macro replicates it.
--
-- Returns NULL when denominator is zero or NULL.
-- Returns NULL when numerator is also NULL.
-- Otherwise returns numerator::float / denominator::float.
--
-- Usage: {{ div0('current_assets', 'current_liabilities') }}

{% macro div0(numerator, denominator) %}
    CASE
        WHEN COALESCE({{ denominator }}, 0) = 0 THEN NULL
        ELSE {{ numerator }}::float / {{ denominator }}::float
    END
{% endmacro %}
