-- Rolling news sentiment features per supplier.
-- Aggregates articles over 7-day and 30-day windows relative to CURRENT_DATE.
--
-- Source:      stg_news_sentiment
-- Grain:       one row per supplier_id (as-of today)
-- Note:        suppliers with no recent news will not appear here.
--              The feature vector handles this via FULL OUTER JOIN with NULL propagation.

with news as (
    select * from {{ ref('stg_news_sentiment') }}
    -- Only articles linked to a resolved supplier
    where supplier_id is not null
      and published_at is not null
),

-- 30-day window (includes the 7-day window)
window_30d as (
    select
        supplier_id,
        avg(sentiment_score)                                as sentiment_score_30d,
        count(*) filter (where sentiment_label = 'negative')
                                                            as negative_article_count_30d,
        count(*)                                            as total_article_count_30d,
        bool_or(topic_layoff)                               as topic_layoff_flag_30d,
        bool_or(topic_bankruptcy)                           as topic_bankruptcy_flag_30d,
        bool_or(topic_strike)                               as topic_strike_flag_30d,
        bool_or(topic_disaster)                             as topic_disaster_flag_30d,
        bool_or(topic_regulatory)                           as topic_regulatory_flag_30d
    from news
    where published_at >= CURRENT_DATE - interval '30 days'
    group by supplier_id
),

-- 7-day window for recent velocity
window_7d as (
    select
        supplier_id,
        avg(sentiment_score)                                as sentiment_score_7d,
        count(*) filter (where sentiment_label = 'negative')
                                                            as negative_article_count_7d
    from news
    where published_at >= CURRENT_DATE - interval '7 days'
    group by supplier_id
),

combined as (
    select
        w30.supplier_id,

        -- Sentiment averages
        w7.sentiment_score_7d,
        w30.sentiment_score_30d,

        -- Article counts
        w30.negative_article_count_30d,
        w30.total_article_count_30d,

        -- Negative article velocity: 7d rate minus 30d average daily rate
        -- Positive value = accelerating negative coverage
        (w7.negative_article_count_7d::float / 7.0)
            - (w30.negative_article_count_30d::float / 30.0)
                                                            as negative_velocity,

        -- Topic flags — true if ANY article in 30d window had this topic
        w30.topic_layoff_flag_30d,
        w30.topic_bankruptcy_flag_30d,
        w30.topic_strike_flag_30d,
        w30.topic_disaster_flag_30d,
        w30.topic_regulatory_flag_30d

    from window_30d w30
    left join window_7d w7 on w30.supplier_id = w7.supplier_id
)

select * from combined
