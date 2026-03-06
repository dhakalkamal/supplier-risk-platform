-- Cleans and validates enriched news sentiment data.
-- One row per article (already deduplicated by the ingestion consumer).
--
-- Source:      enriched.stg_news_sentiment (written by NewsEnrichmentConsumer)
-- Grain:       one row per article_id
-- Dedup:       consumer uses ON CONFLICT DO UPDATE — table is already deduplicated
-- Idempotent:  yes — running twice produces the same result
--
-- Note: the source table is written by the news ingestion pipeline directly
-- (post-NLP enrichment). This model applies additional type casting and
-- filters out rows with invalid sentiment scores before they reach marts.

with source as (
    select * from {{ source('enriched', 'stg_news_sentiment') }}
),

cleaned as (
    select
        -- Identity
        article_id::varchar                             as article_id,
        supplier_id::varchar                            as supplier_id,
        supplier_name_raw::varchar                      as supplier_name_raw,

        -- Content
        title::varchar                                  as title,
        url::varchar                                    as url,
        published_at::timestamp                         as published_at,
        source_name::varchar                            as source_name,

        -- Sentiment (FinBERT output)
        sentiment_score::float                          as sentiment_score,
        sentiment_label::varchar                        as sentiment_label,

        -- Topic flags
        topic_layoff::boolean                           as topic_layoff,
        topic_bankruptcy::boolean                       as topic_bankruptcy,
        topic_strike::boolean                           as topic_strike,
        topic_disaster::boolean                         as topic_disaster,
        topic_regulatory::boolean                       as topic_regulatory,

        -- Quality signals
        source_credibility::float                       as source_credibility,
        word_count::int                                 as word_count,

        -- Provenance
        processed_at::timestamp                         as processed_at,
        CURRENT_TIMESTAMP                               as _dbt_ingested_at

    from source
    where article_id is not null
      and published_at is not null
      -- Reject clearly invalid sentiment scores — these indicate NLP pipeline errors
      and sentiment_score between -1.0 and 1.0
)

select * from cleaned
