# FIRST_PROMPT.md — Session 1: Project Scaffold + SEC EDGAR Pipeline

---

## HOW TO USE THIS FILE

Say this to Claude Code:
```
Read prompts/FIRST_PROMPT.md and follow it exactly.
```

Do NOT run Session 2–5 prompts until this session is fully complete.
Each session has its own file in the `prompts/` folder.

---

## CONTEXT LOADING (do this first, before any code)

Read these files in this exact order before writing a single line of code:
1. `CLAUDE.md`
2. `docs/ARCHITECTURE.md`
3. `docs/DATA_SOURCES.md`
4. `specs/ML_SPEC.md`

After reading, confirm by saying:
> "I have read all 4 spec files. Here is my understanding of what we are building and what Phase 1 requires: [summary]"

Do not proceed until this confirmation is given.

---

## IMPORTANT RULES FOR THIS SESSION

These override any other instinct you have:

- **Work through steps sequentially. Never skip ahead.**
- **After completing each step, stop and explicitly say: "✅ Step N complete. Running tests now."**
- **Run `make test` after Steps 3, 4, and 5. Do not proceed if tests fail — fix them first.**
- **If you hit an ambiguity or a blocker, stop and ask. Do not guess and continue.**
- **Snowflake does not exist locally yet. Any code that touches Snowflake must use a local mock or be behind an interface that can be swapped. Do not make live Snowflake calls.**
- **Do not read or act on the Session 2–5 prompts in this file — those are in separate files for future sessions.**

---

## STEP 1: Project Scaffold

Create the following directory and file structure. Create real files, not empty placeholders — every `__init__.py` should have a module docstring, every config file should have real structure.

```
supplier-risk-platform/
├── CLAUDE.md                        (already exists)
├── docs/                            (already exists)
├── specs/                           (already exists)
├── prompts/                         (already exists)
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app factory
│   │   ├── config.py                # pydantic-settings Settings class
│   │   ├── models/
│   │   │   └── __init__.py
│   │   ├── repositories/
│   │   │   └── __init__.py
│   │   ├── services/
│   │   │   └── __init__.py
│   │   └── api/
│   │       └── v1/
│   │           ├── __init__.py
│   │           └── routes/
│   │               └── __init__.py
│   └── tests/
│       └── __init__.py
├── data/
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── sec_edgar/
│   │   │   ├── __init__.py
│   │   │   ├── scraper.py
│   │   │   ├── parser.py
│   │   │   └── models.py
│   │   ├── news/
│   │   │   └── __init__.py
│   │   ├── ais/
│   │   │   └── __init__.py
│   │   └── macro/
│   │       └── __init__.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── kafka_producer.py
│   │   ├── kafka_consumer.py
│   │   └── entity_resolution.py
│   ├── dags/
│   │   └── __init__.py
│   └── dbt/
│       ├── dbt_project.yml
│       ├── profiles.yml.example
│       ├── models/
│       │   ├── raw/
│       │   ├── staging/
│       │   └── marts/
│       └── tests/
├── ml/
│   ├── __init__.py
│   ├── features/
│   │   └── __init__.py
│   ├── training/
│   │   └── __init__.py
│   ├── serving/
│   │   └── __init__.py
│   └── evaluation/
│       └── __init__.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── ingestion/
│   │   └── __init__.py
│   └── pipeline/
│       └── __init__.py
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── pyproject.toml
└── Makefile
```

**When Step 1 is done, say: "✅ Step 1 complete — scaffold created."**
**Then immediately proceed to Step 2. Do not wait.**

---

## STEP 2: Core Configuration Files

### `backend/app/config.py`
Use `pydantic-settings`. Every setting must have a type annotation and a description in the docstring. Group settings into logical nested classes:

```python
class Settings(BaseSettings):
    # App
    environment: Literal["dev", "staging", "prod"]
    debug: bool

    # Snowflake (not used locally — mocked via interface)
    snowflake_account: str
    snowflake_user: str
    snowflake_password: str
    snowflake_database: str
    snowflake_warehouse: str
    snowflake_schema: str

    # Kafka
    kafka_bootstrap_servers: str
    kafka_security_protocol: str

    # Redis
    redis_url: str

    # SEC EDGAR
    sec_edgar_user_agent: str   # format: "AppName email@domain.com"
    sec_edgar_base_url: str     # default: "https://data.sec.gov"
    sec_edgar_rate_limit: int   # default: 10 (requests per second)

    # News API
    news_api_key: str

    # Auth0
    auth0_domain: str
    auth0_audience: str

    # Postgres
    database_url: str
```

### `.env.example`
Every single variable from `config.py` must appear here with a descriptive comment and a fake example value. No variable should be undocumented.

### `docker-compose.yml`
Services required for local development:
- `postgres:15-alpine` on port 5432
- `redis:7-alpine` on port 6379
- `bitnami/kafka:latest` + `bitnami/zookeeper` on ports 9092/2181
- `apache/airflow:2.8.0` webserver + scheduler (LocalExecutor, no Celery for dev)
- `mailhog/mailhog` on ports 1025/8025

All services must have health checks. Kafka must wait for Zookeeper to be healthy before starting.

### `requirements.txt` and `requirements-dev.txt`

`requirements.txt` — production deps with pinned versions:
```
fastapi==0.109.0
uvicorn[standard]==0.27.0
pydantic==2.6.0
pydantic-settings==2.1.0
httpx==0.26.0
structlog==24.1.0
aiokafka==0.10.0
snowflake-connector-python==3.6.0
sqlalchemy==2.0.25
asyncpg==0.29.0
tenacity==8.2.3
python-dateutil==2.8.2
```

`requirements-dev.txt`:
```
pytest==7.4.4
pytest-asyncio==0.23.3
pytest-cov==4.1.0
respx==0.20.2
ruff==0.2.0
mypy==1.8.0
```

### `pyproject.toml`
Configure ruff (line length 100, select E/W/F/I), mypy (strict mode), pytest (asyncio_mode=auto).

### `Makefile`
```makefile
make setup      # pip install -r requirements.txt -r requirements-dev.txt, cp .env.example .env
make dev        # docker-compose up -d, wait for health checks
make test       # pytest tests/ -v --cov=data --cov=backend --cov-report=term-missing
make lint       # ruff check . && mypy data/ backend/
make ingest-sec # python -m data.ingestion.sec_edgar.scraper --run-once
make dbt-run    # dbt run --project-dir data/dbt
make down       # docker-compose down
```

### `tests/conftest.py`
Create shared pytest fixtures:
- `mock_settings` — Settings object with test values, no real credentials
- `mock_kafka_producer` — async mock that captures published messages
- `mock_httpx_client` — for mocking SEC API responses

**When Step 2 is done, run: `make lint`**
**If lint fails, fix all errors before proceeding.**
**Say: "✅ Step 2 complete — lint passing."**

---

## STEP 3: SEC EDGAR Ingestion Pipeline

Build `data/ingestion/sec_edgar/`. This is the most important step — get it right.

### `models.py` — Build this first

```python
# All Pydantic v2 models. Every field typed. No Optional without a default.

class CompanySearchResult(BaseModel):
    cik: str                    # zero-padded to 10 digits
    canonical_name: str
    tickers: list[str]
    exchanges: list[str]

class Filing(BaseModel):
    cik: str
    filing_type: str            # "10-K", "10-Q", "8-K"
    filed_date: date
    period_of_report: date
    accession_number: str
    primary_document: str

class FinancialSnapshot(BaseModel):
    cik: str
    period_end: date
    filing_type: str
    total_assets: float | None
    current_assets: float | None
    total_liabilities: float | None
    current_liabilities: float | None
    retained_earnings: float | None
    shareholders_equity: float | None
    revenue: float | None
    ebit: float | None
    net_income: float | None
    cash: float | None
    long_term_debt: float | None
    interest_expense: float | None
    inventory: float | None
    # Derived
    altman_z_score: float | None
    going_concern_flag: bool
    financial_data_staleness_days: int
    source_url: str
    ingested_at: datetime

class SECRawEvent(BaseModel):
    """Schema for raw.sec Kafka topic."""
    source: Literal["sec_edgar"]
    cik: str
    company_name: str
    filing_type: str
    filed_date: date
    period_of_report: date
    financials: FinancialSnapshot
    going_concern: bool
    ingested_at: datetime
```

### `scraper.py`

```python
class SECEdgarClient:
    """Async client for the SEC EDGAR API.
    
    Respects the SEC's rate limit of 10 requests/second.
    Uses exponential backoff on 429 and 503 responses.
    All requests are logged with structlog.
    """
    
    async def get_company_submissions(self, cik: str) -> CompanySubmissions: ...
    async def get_company_facts(self, cik: str) -> CompanyFacts: ...
    async def search_company(self, company_name: str) -> list[CompanySearchResult]: ...
    async def get_recent_filings(self, since_date: date) -> list[Filing]: ...
```

Requirements:
- `httpx.AsyncClient` with a shared client instance (not a new client per request)
- `asyncio.Semaphore(10)` for rate limiting
- `tenacity` for retry logic: retry on 429/503, max 3 attempts, exponential backoff starting at 2s
- CIK must always be zero-padded to 10 digits: `cik.zfill(10)`
- User-Agent header set from `settings.sec_edgar_user_agent`
- Every request logged: `log.info("sec_edgar.request", url=url, cik=cik)`
- Every response logged: `log.info("sec_edgar.response", status=response.status_code, cik=cik)`

### `parser.py`

```python
class SECFinancialsParser:
    """Extracts structured financial data from SEC EDGAR XBRL company facts.
    
    Handles the messiness of XBRL: multiple concept names for the same field,
    different units, missing data, and stale filings.
    """
    
    # XBRL field mappings — multiple possible concept names per field
    REVENUE_CONCEPTS = [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ]
    ASSET_CONCEPTS = ["Assets"]
    # ... etc for all fields
    
    def extract_financials(self, cik: str, company_facts: dict) -> FinancialSnapshot: ...
    def compute_altman_z_score(self, f: FinancialSnapshot) -> float | None: ...
    def detect_going_concern(self, filing_text: str) -> bool: ...
    def get_latest_value(self, facts: dict, concepts: list[str]) -> float | None: ...
```

Altman Z-Score formula (use the private company version):
```
Z' = 0.717*X1 + 0.847*X2 + 3.107*X3 + 0.420*X4 + 0.998*X5
X1 = working_capital / total_assets
X2 = retained_earnings / total_assets  
X3 = ebit / total_assets
X4 = book_equity / total_liabilities
X5 = revenue / total_assets

Z' < 1.23  → distress zone
1.23–2.90  → grey zone
Z' > 2.90  → safe zone
Return None if any required field is missing
```

Going concern detection — search for these phrases in 10-K text (case-insensitive):
```python
GOING_CONCERN_PHRASES = [
    "substantial doubt about its ability to continue as a going concern",
    "going concern doubt",
    "ability to continue as a going concern",
    "raise substantial doubt",
]
```

**When Step 3 is done, run: `make lint`**
**Fix all type errors and linting issues.**
**Say: "✅ Step 3 complete — lint passing."**

---

## STEP 4: Kafka Producer

Build `data/pipeline/kafka_producer.py`.

```python
class SupplierRiskKafkaProducer:
    """Publishes validated supply chain risk events to Kafka.
    
    Features:
    - Schema validation via Pydantic before every publish
    - Dead-letter queue routing for failed/invalid records
    - Structured logging on every operation
    - Async context manager for clean lifecycle management
    """
    
    TOPIC_MAP = {
        "sec": "raw.sec",
        "news": "raw.news", 
        "ais": "raw.ais",
        "macro": "raw.macro",
        "geo": "raw.geo",
    }
    DLQ_TOPIC_PREFIX = "raw.dlq"
    
    async def publish_sec_event(self, event: SECRawEvent) -> bool: ...
    async def publish_to_dlq(self, topic: str, raw_payload: dict, error: str) -> None: ...
    async def __aenter__(self): ...
    async def __aexit__(self): ...
```

Rules:
- Validate with Pydantic before publishing — if validation fails, route to DLQ, do not raise
- DLQ message must include: original payload, error message, timestamp, source topic
- Log every successful publish: `log.info("kafka.published", topic=topic, cik=cik)`
- Log every DLQ send: `log.warning("kafka.dlq", topic=dlq_topic, error=error, cik=cik)`
- Use `aiokafka.AIOKafkaProducer`

**When Step 4 is done, say: "✅ Step 4 complete."**

---

## STEP 5: Tests

Write tests for Steps 3 and 4. These must be real tests, not smoke tests.

### `tests/ingestion/test_sec_scraper.py`

Use `respx` to mock all HTTP calls. Test:
- `get_company_submissions` — happy path, returns correctly parsed object
- `get_company_submissions` — 429 response triggers retry with backoff
- `get_company_submissions` — 503 response triggers retry
- `get_company_submissions` — 3 consecutive failures raises exception
- `search_company` — returns list of CompanySearchResult
- CIK zero-padding: input "789019" → request uses "0000789019"
- Rate limiting: 15 concurrent calls use semaphore correctly

### `tests/ingestion/test_sec_parser.py`

Use real XBRL fixture data (create a `tests/fixtures/sec_company_facts.json` with realistic data).
Test:
- `extract_financials` — correctly extracts all fields from fixture
- `compute_altman_z_score` — test with known values, verify against manual calculation
- `compute_altman_z_score` — returns None when total_assets is None
- `compute_altman_z_score` — returns None when revenue is None
- `detect_going_concern` — returns True for each phrase in GOING_CONCERN_PHRASES
- `detect_going_concern` — returns False for normal 10-K text
- `get_latest_value` — returns most recent value when multiple periods exist
- `get_latest_value` — tries fallback concepts when primary is missing

### `tests/pipeline/test_kafka_producer.py`

Use the `mock_kafka_producer` fixture from conftest. Test:
- `publish_sec_event` — valid event publishes to `raw.sec` topic
- `publish_sec_event` — message is JSON-serialisable
- `publish_sec_event` — invalid event (missing required field) routes to DLQ
- DLQ message contains original payload + error string + timestamp
- Context manager properly closes producer on exit

**After writing tests, run: `make test`**
**Tests must pass before continuing. If any fail, fix them now.**
**Coverage must be ≥ 80% on `data/ingestion/sec_edgar/` and `data/pipeline/kafka_producer.py`.**
**Say: "✅ Step 5 complete — X tests passing, Y% coverage."**

---

## STEP 6: Airflow DAG

Create `data/dags/ingest_sec_edgar.py`.

This file defines the schedule — it does NOT run the ingestion. The ingestion code lives in `data/ingestion/sec_edgar/`. The DAG just orchestrates it.

```python
"""
SEC EDGAR Ingestion DAG

Schedule: Daily at 02:00 UTC
Tasks:
    1. fetch_new_filings    — call SECEdgarClient.get_recent_filings(since_date=yesterday)
    2. parse_financials     — for each filing, call SECFinancialsParser.extract_financials()
    3. publish_to_kafka     — publish each FinancialSnapshot to raw.sec via KafkaProducer
    4. update_ingestion_log — write run metadata to postgres (run_date, count, errors)

Retry: 3 attempts, 5-minute delay between retries
On failure: log structured error (do NOT send email yet — that's Phase 2)
"""

from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {
    "owner": "data-team",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "on_failure_callback": log_failure_to_structlog,  # implement this
}

with DAG(
    dag_id="ingest_sec_edgar",
    schedule="0 2 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ingestion", "sec", "phase-1"],
) as dag:
    ...
```

Each task function must:
- Accept `**context` from Airflow
- Use XCom to pass filing list between tasks (not a database)
- Log start/end/count with structlog
- Handle empty results gracefully (0 new filings = success, not failure)

**When Step 6 is done, say: "✅ Step 6 complete."**

---

## STEP 7: Makefile

Create the `Makefile`. Every target must actually work — test each one.

```makefile
.PHONY: setup dev down test lint ingest-sec dbt-run

setup:
	pip install -r requirements.txt -r requirements-dev.txt
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example — fill in your values"; fi

dev:
	docker-compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
	@docker-compose ps

down:
	docker-compose down -v

test:
	pytest tests/ -v --cov=data --cov=backend --cov-report=term-missing --cov-fail-under=80

lint:
	ruff check .
	mypy data/ backend/ --ignore-missing-imports

ingest-sec:
	python -m data.ingestion.sec_edgar.scraper --run-once --since-date yesterday

dbt-run:
	dbt run --project-dir data/dbt --profiles-dir data/dbt

help:
	@echo "Available commands:"
	@echo "  make setup      - Install dependencies"
	@echo "  make dev        - Start local services"
	@echo "  make down       - Stop local services"
	@echo "  make test       - Run tests with coverage"
	@echo "  make lint       - Run ruff + mypy"
	@echo "  make ingest-sec - Trigger SEC ingestion manually"
	@echo "  make dbt-run    - Run dbt models"
```

**When Step 7 is done, run: `make lint` then `make test`**
**Both must pass clean.**
**Say: "✅ Step 7 complete — lint clean, all tests passing."**

---

## SESSION 1 DONE — FINAL CHECKLIST

Before ending this session, verify every item:

```
□ Directory structure matches the scaffold exactly
□ make lint passes with zero errors (ruff + mypy)
□ make test passes with ≥80% coverage on sec_edgar/ and kafka_producer.py
□ docker-compose.yml has all 5 services with health checks
□ .env.example has every variable with a comment
□ SECEdgarClient rate-limits to 10 req/sec using asyncio.Semaphore
□ SECEdgarClient retries on 429/503 using tenacity
□ Altman Z-Score returns None when data is missing (not 0.0)
□ Going concern detection tests cover all phrases in GOING_CONCERN_PHRASES
□ Kafka producer routes invalid records to DLQ, never raises on bad data
□ Airflow DAG has correct cron schedule "0 2 * * *"
□ No Snowflake live calls — all Snowflake code is behind an interface
□ No print() statements anywhere — structlog only
□ No raw dicts passed between functions — Pydantic models only
```

**Say the final status: "Session 1 complete. Checklist: X/14 items green."**

If any item is red, fix it before declaring done.

---

## WHAT COMES NEXT

Session 2 prompt is in `prompts/SESSION_2.md`.
Start it in a **new Claude Code session** after you have:
1. Reviewed the code yourself
2. Run `make dev` and confirmed docker-compose starts cleanly
3. Manually triggered `make ingest-sec` and seen at least one record hit the Kafka topic
