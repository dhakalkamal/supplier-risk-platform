# SESSION_2.md — News Ingestion + NLP Pipeline

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md, then read prompts/SESSION_2.md and follow it exactly.
```

Only start this session after Session 1 checklist is fully green.

---

## CONTEXT CHECK (do before any code)

Read these files:
1. `CLAUDE.md`
2. `docs/DATA_SOURCES.md` — specifically the "Source 2: News APIs" section
3. `specs/ML_SPEC.md` — specifically "Category 2: News Sentiment Features"

Confirm by saying:
> "I have read the specs. Session 1 built [X]. Today I am building [Y]."

---

## RULES FOR THIS SESSION

- Follow the same patterns established in Session 1 (same logging, same Pydantic models, same error handling style)
- Run `make lint` after each step. Fix before proceeding.
- Run `make test` after Step 3. Must pass before Step 4.
- Snowflake writes must go through an interface/repository — no direct connector calls in business logic.
- If FinBERT model download fails (network issue), implement a fallback to a simple lexicon-based scorer. Never block on model availability.

---

## STEP 1: News Ingestion Models

Create `data/ingestion/news/models.py`:

```python
class RawArticle(BaseModel):
    """Raw article as received from NewsAPI or GDELT."""
    article_id: str              # sha256 of URL
    url: str
    title: str
    content: str | None          # can be None from some sources
    published_at: datetime
    source_name: str
    source_credibility: float    # 0.0–1.0, assigned by source
    ingested_at: datetime
    ingestion_source: Literal["newsapi", "gdelt", "rss"]

class EnrichedArticle(BaseModel):
    """Article after NLP processing."""
    article_id: str
    supplier_id: str | None      # None if entity resolution failed
    supplier_name_raw: str | None
    title: str
    url: str
    published_at: datetime
    source_name: str
    sentiment_score: float       # -1.0 to 1.0
    sentiment_label: Literal["positive", "negative", "neutral"]
    topic_layoff: bool
    topic_bankruptcy: bool
    topic_strike: bool
    topic_disaster: bool
    topic_regulatory: bool
    source_credibility: float
    word_count: int
    processed_at: datetime

class NewsRawEvent(BaseModel):
    """Schema for raw.news Kafka topic."""
    source: Literal["newsapi", "gdelt"]
    article_id: str
    url: str
    title: str
    content: str | None
    published_at: datetime
    source_name: str
    ingested_at: datetime
```

---

## STEP 2: News API Client

Create `data/ingestion/news/scraper.py`:

```python
class NewsAPIClient:
    """Client for NewsAPI.org.
    
    Fetches articles mentioning supplier names.
    Handles pagination, rate limiting, and deduplication by article_id.
    """
    
    async def fetch_articles_for_supplier(
        self,
        company_name: str,
        from_date: date,
        to_date: date,
    ) -> list[RawArticle]: ...
    
    async def fetch_recent_articles(
        self,
        query: str,
        hours_back: int = 24,
    ) -> list[RawArticle]: ...

class GDELTClient:
    """Fallback client for GDELT (free, no API key required).
    
    Lower quality than NewsAPI but free and has historical data.
    Used as fallback if NewsAPI quota is exhausted.
    """
    
    async def fetch_articles(
        self,
        query: str,
        max_records: int = 250,
    ) -> list[RawArticle]: ...
```

Source credibility scores (hardcode these):
```python
SOURCE_CREDIBILITY = {
    "reuters.com": 1.0,
    "bloomberg.com": 1.0,
    "apnews.com": 1.0,
    "ft.com": 0.95,
    "wsj.com": 0.95,
    "cnbc.com": 0.85,
    "businessinsider.com": 0.70,
    "default": 0.50,
}
```

---

## STEP 3: NLP Processing Worker

Create `data/ingestion/news/nlp_processor.py`:

```python
class NLPProcessor:
    """Processes raw articles: sentiment, topic classification, entity linking.
    
    Uses FinBERT for sentiment (ProsusAI/finbert).
    Falls back to lexicon-based scorer if model unavailable.
    Topic classification uses keyword matching (ML classifier in Phase 2).
    Entity linking is a simple fuzzy match against supplier registry — 
    full resolution pipeline is built in Session 3.
    """
    
    def __init__(self, use_finbert: bool = True): ...
    
    async def process_article(self, article: RawArticle) -> EnrichedArticle: ...
    
    def get_sentiment(self, text: str) -> tuple[float, str]: ...
    # Returns: (score -1.0 to 1.0, label "positive"/"negative"/"neutral")
    
    def classify_topics(self, text: str) -> dict[str, bool]: ...
    # Returns: {"layoff": bool, "bankruptcy": bool, "strike": bool, ...}
    
    def extract_company_mentions(self, text: str) -> list[str]: ...
    # Simple NER — just return strings for now, resolution in Session 3
```

Topic keywords (implement `classify_topics` using these):
```python
TOPIC_KEYWORDS = {
    "layoff": ["layoff", "layoffs", "redundan", "workforce reduction", "job cut", "retrench"],
    "bankruptcy": ["bankruptcy", "chapter 11", "insolvency", "administration", "liquidat"],
    "strike": ["strike", "industrial action", "walkout", "work stoppage", "labor dispute"],
    "disaster": ["fire", "explosion", "flood", "earthquake", "hurricane", "facility damage"],
    "regulatory": ["fined", "penalty", "recall", "shutdown order", "violation", "sanction"],
}
```

FinBERT fallback — if model load fails, use this lexicon scorer:
```python
NEGATIVE_WORDS = ["bankrupt", "layoff", "loss", "decline", "fail", "risk", "debt", "warn"]
POSITIVE_WORDS = ["profit", "growth", "expand", "award", "record", "strong", "beat"]
# Simple count-based scorer: (positive_count - negative_count) / total_words
```

---

## STEP 4: Kafka Consumer + Snowflake Writer

Create `data/ingestion/news/consumer.py`:

```python
class NewsEnrichmentConsumer:
    """Consumes raw.news, enriches via NLP, writes to Snowflake staging.
    
    Reads from: raw.news Kafka topic
    Writes to:  staging.stg_news_sentiment (Snowflake, via repository interface)
    On error:   route to raw.dlq.news
    """
    
    async def run(self) -> None:
        """Main consumer loop. Runs indefinitely until cancelled."""
        ...
    
    async def process_message(self, message: NewsRawEvent) -> None: ...
```

The Snowflake write must go through a repository interface:
```python
class NewsRepository(Protocol):
    """Interface for writing enriched articles. Swappable for testing."""
    async def upsert_enriched_article(self, article: EnrichedArticle) -> None: ...
    async def article_exists(self, article_id: str) -> bool: ...
```

Implement two versions:
- `SnowflakeNewsRepository` — real implementation
- `InMemoryNewsRepository` — for tests, stores articles in a list

---

## STEP 5: Airflow DAG

Create `data/dags/ingest_news.py`:
- Schedule: every 2 hours (`"0 */2 * * *"`)
- Tasks: `fetch_articles` → `enrich_articles` → `write_to_staging` → `publish_to_kafka`
- Same retry/logging pattern as `ingest_sec_edgar` DAG
- Handle partial failures: if NLP fails for one article, log and continue — don't fail the whole run

---

## STEP 6: Tests

### `tests/ingestion/test_news_scraper.py`
- Happy path: fetch articles returns list of RawArticle
- article_id is sha256 of URL (deterministic)
- Duplicate URLs produce same article_id
- Source credibility assigned correctly by domain
- GDELT fallback called when NewsAPI returns 429

### `tests/ingestion/test_nlp_processor.py`
- `classify_topics` returns True for each keyword category
- `classify_topics` is case-insensitive
- `get_sentiment` returns score in range [-1.0, 1.0]
- `get_sentiment` returns "negative" for clearly negative text
- Fallback lexicon scorer works when FinBERT unavailable
- `process_article` returns EnrichedArticle with all fields populated

### `tests/ingestion/test_news_consumer.py`
- Valid message processed and passed to repository
- Invalid message routed to DLQ
- `InMemoryNewsRepository.upsert_enriched_article` stores correctly
- Duplicate article_id is upserted, not duplicated

**Run `make test` — must pass with ≥80% coverage on `data/ingestion/news/`.**

---

## SESSION 2 DONE — CHECKLIST

```
□ make lint passes clean
□ make test passes — ≥80% coverage on data/ingestion/news/
□ RawArticle.article_id is always sha256(url) — deterministic
□ FinBERT fallback implemented — NLP never blocks on model availability
□ NewsRepository is a Protocol — Snowflake and InMemory implementations exist
□ consumer.py routes failed articles to DLQ, never raises and stops
□ No direct Snowflake connector calls in business logic
□ Topic classification is case-insensitive
□ Source credibility scores hardcoded correctly
□ Airflow DAG schedule is "0 */2 * * *"
```

**Say: "Session 2 complete. Checklist: X/10 items green."**

Next: `prompts/SESSION_3.md`
