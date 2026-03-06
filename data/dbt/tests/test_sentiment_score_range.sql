-- Custom test: sentiment scores must be in [-1.0, 1.0].
--
-- FinBERT outputs are bounded to this range by design. Scores outside this range
-- indicate a NLP pipeline bug or a schema mismatch during ingestion.
-- The staging model already filters these out, so any rows here mean
-- data reached the enriched source with invalid scores — investigate immediately.
-- The test passes when this query returns zero rows.

select
    article_id,
    sentiment_score
from {{ ref('stg_news_sentiment') }}
where sentiment_score < -1.0
   or sentiment_score > 1.0
