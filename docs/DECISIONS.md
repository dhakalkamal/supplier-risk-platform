# DECISIONS.md — Architecture Decision Records (ADRs)

> Every significant technical decision is recorded here with context and rationale.
> Before making ANY architectural choice, check this file first.
> If a decision is already recorded — follow it.
> If you disagree — propose a new ADR at the bottom. Never silently deviate.
> Never modify an Accepted ADR — add a superseding ADR instead.

---

## ADR Index

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Python 3.11 for backend + ML | ✅ Accepted |
| ADR-002 | Postgres as data store for prototype | ✅ Accepted |
| ADR-003 | XGBoost as primary risk model | ✅ Accepted |
| ADR-004 | Auth0 for authentication | ✅ Accepted |
| ADR-005 | AWS MSK for managed Kafka | ✅ Accepted |
| ADR-006 | Row-level security for multi-tenancy | ✅ Accepted |
| ADR-007 | Heuristic scorer before ML model | ✅ Accepted |
| ADR-008 | dbt for all SQL transformations | ✅ Accepted |
| ADR-009 | Hybrid entity resolution (rules + LLM) | ✅ Accepted |
| ADR-010 | Repository pattern for all data access | ✅ Accepted |
| ADR-011 | Pydantic v2 for all data models | ✅ Accepted |
| ADR-012 | Celery for background tasks | ✅ Accepted |
| ADR-013 | No live external calls in tests | ✅ Accepted |
| ADR-014 | Frozen RiskScoreOutput schema | ✅ Accepted |
| ADR-015 | Conda (genai env) for Python environment | ✅ Accepted |
| ADR-016 | FinBERT for news sentiment with lexicon fallback | ✅ Accepted |

---

## ADR-001: Python 3.11 for Backend and ML

**Status:** Accepted
**Date:** Project init

**Context:** Need to choose primary language for backend API and ML pipeline.

**Decision:** Python 3.11 for both backend (FastAPI) and all ML/data pipeline code.

**Rationale:**
- ML ecosystem is Python-native: XGBoost, HuggingFace, SHAP, MLflow, lifelines, scikit-learn
- FastAPI is production-grade, async, fully typed — not a "ML glue" framework
- Single language across the entire codebase — no context switching, no polyglot dependency hell
- Python 3.11 specifically: 25% faster than 3.10, improved error messages, stable enough for production
- Hiring: Python ML engineers vastly outnumber Go+Python or Rust+Python hybrid candidates

**Trade-offs:**
- Slower than Go/Rust for CPU-bound work — mitigated because our bottlenecks are I/O (database queries, API calls), not CPU
- GIL limits true thread parallelism — mitigated by async I/O (httpx, aiokafka) and Celery multi-process workers
- Memory usage higher than compiled languages — acceptable at our scale

**Constraints this creates:**
- All code must use Python 3.11 syntax — no 3.9 or 3.10 compatibility required
- Type hints required on all functions (mypy enforced)
- See ADR-015 for environment management

---

## ADR-002: Postgres as Data Store for Prototype

**Status:** Accepted
**Date:** Project init

**Context:** Need a data store for raw signal storage, dbt transformations, and ML feature engineering. Choosing the right tool for the prototype stage vs. future scale.

**Decision:** Postgres 15 for all data — both the operational API database and the pipeline/analytics schema. A single Postgres instance with two schemas: `public` (operational) and `pipeline` (ETL + ML data).

**Rationale:**
- Zero additional infrastructure: Postgres is already in the stack for the operational API database
- dbt supports Postgres natively — no change to transformation logic
- Eliminates the Snowflake sync step: ML pipeline reads feature vectors and writes scores to the same Postgres instance the API uses
- JSONB type handles semi-structured payloads well (equivalent to Snowflake VARIANT for our use case)
- At prototype scale (<500GB, <200K suppliers), Postgres query performance is more than adequate
- Simpler local dev: no cloud credentials or Snowflake account required to run the pipeline locally

**When to revisit:**
- Data volume exceeds ~500GB in the pipeline schema, OR
- Complex analytical queries (e.g. 90-day rolling window aggregations across 200K+ suppliers) start running slowly
- At that point: migrate pipeline schema to Snowflake or BigQuery. dbt models are portable — the migration is a configuration change, not a rewrite.

**What this means for code:**
- All pipeline tables in `pipeline` schema in Postgres (see ARCHITECTURE.md Section 5)
- dbt runs against Postgres: `profiles.yml` uses `type: postgres`
- Use `JSONB` for semi-structured fields (not Snowflake's `VARIANT`)
- Use `TIMESTAMPTZ` for all timestamps (not Snowflake's `TIMESTAMP_NTZ`)
- Use `TEXT[]` for array fields (not Snowflake's `ARRAY`)
- Use `gen_random_uuid()` for UUIDs (not Snowflake's `UUID_STRING()`)
- InMemory repositories in tests — never connect to real Postgres in unit tests

---

## ADR-003: XGBoost as Primary Risk Model

**Status:** Accepted
**Date:** Project init

**Context:** Choosing the primary ML model architecture for supplier risk scoring.

**Decision:** XGBoost gradient boosting as the primary model. FinBERT for NLP features (separate, used as a feature generator).

**Rationale:**
1. **Explainability is a hard product requirement.** SHAP values are native to tree models (TreeExplainer — fast, exact). Neural nets require KernelExplainer — slow and approximate. Procurement managers will not act on unexplained scores.
2. **Data volume at MVP.** We will have thousands of supplier-snapshots, not millions. Neural nets consistently underperform gradient boosting on small tabular datasets.
3. **Training speed.** XGBoost trains in minutes on a laptop. Retraining weekly is practical. Neural nets train in hours and require GPU infrastructure.
4. **Industry precedent.** Credit risk teams at every major bank use gradient boosting (LightGBM/XGBoost) for default prediction — the problem framing is identical to ours.
5. **Sales credibility.** "We use XGBoost with SHAP explanations, the same approach used in credit risk" is more credible to enterprise buyers than "our neural network says 73."

**Trade-offs:**
- Neural nets may outperform on text features at scale — mitigated by using FinBERT as a feature generator (produces numeric embeddings that XGBoost consumes)
- XGBoost does not naturally handle temporal patterns — mitigated by engineering rolling window features (7d, 30d, 90d) explicitly
- Revisit neural architecture only if: dataset exceeds 100K labelled disruption events AND XGBoost PR-AUC plateaus below 0.50

**What this means for code:**
- Primary model: `xgboost.XGBClassifier`
- Explainability: `shap.TreeExplainer` (not KernelExplainer)
- NLP features: `ProsusAI/finbert` produces sentiment scores that XGBoost consumes
- Never deploy a model without SHAP values — it violates this ADR
- Cox PH survival model: future consideration once we have 500+ labelled disruption events

---

## ADR-004: Auth0 for Authentication

**Status:** Accepted
**Date:** Project init

**Context:** Need production-grade auth supporting SSO for enterprise tenants, MFA, and JWT issuance.

**Decision:** Auth0 (Okta Customer Identity Cloud)

**Rationale:**
- Never build authentication. It is a commodity with catastrophic failure modes.
- Auth0 supports: email/password, Google OAuth, SAML SSO (enterprise requirement), MFA, magic links
- JWT validation is a 10-line FastAPI middleware — trivial to integrate
- Cost: ~$23/month for 1,000 MAU. Negligible vs. engineering time to build equivalent.
- Auth0 handles: password hashing, brute force protection, session management, token rotation

**Trade-offs:**
- Vendor dependency — mitigated: Auth0 (Okta) is the industry standard, JWKS-based JWT validation makes migration possible
- Cost scales at 10K+ MAU — revisit pricing at that point (Clerk or self-hosted Keycloak as alternatives)
- Adds Auth0 as a dependency in the critical path — mitigated by caching JWKS keys locally

**What this means for code:**
- Never write password hashing, session management, or token generation code
- JWT validation middleware reads `tenant_id` and `role` from JWT claims
- Tests use mock JWT tokens — never call Auth0 in tests (see ADR-013)

---

## ADR-005: AWS MSK for Managed Kafka

**Status:** Accepted
**Date:** Project init
**Supersedes:** Original mention of Confluent Cloud in early drafts

**Context:** Need a managed Kafka service for event streaming. Two main options evaluated.

**Decision:** AWS MSK (Managed Streaming for Apache Kafka)

**Options evaluated:**
- A. Confluent Cloud — ~$200/month, best Kafka tooling, vendor-managed
- B. AWS MSK — ~$150/month, AWS-native, IAM integration
- C. Self-hosted Kafka on Kubernetes — cheapest, highest operational burden

**Rationale for AWS MSK:**
- Already on AWS — IAM integration means no separate credential management for Kafka
- ~25% cheaper than Confluent Cloud at our scale
- Managed service — no Kafka operational burden (broker management, upgrades, replication)
- MSK Serverless available for MVP: pay per GB, no broker sizing decisions
- Confluent's extra tooling (Schema Registry, ksqlDB) not needed at our stage

**Why not self-hosted:**
- Kafka operations is a full-time job — not appropriate for a small team
- Broker failover, partition rebalancing, log compaction tuning — all handled by MSK

**Local development:**
- `bitnami/kafka` Docker image — same API, no AWS credentials needed locally
- See `docker-compose.yml` for local Kafka configuration

**What this means for code:**
- Use `aiokafka` Python client — works with both local Docker and MSK
- MSK uses IAM authentication in production — `aiokafka` configured with AWS credentials
- Local uses PLAINTEXT — no auth required
- Topic auto-creation enabled in local dev, disabled in production (topics created via Terraform)

---

## ADR-006: Row-Level Security for Multi-Tenancy

**Status:** Accepted
**Date:** Project init

**Context:** How to isolate tenant data in Postgres while keeping a single database.

**Decision:** Row-level security (RLS) in Postgres, enforced at both database layer AND application layer (defence in depth).

**Options evaluated:**
- A. Schema-per-tenant — separate Postgres schema per customer
- B. Database-per-tenant — separate Postgres instance per customer
- C. Row-level security — single schema, `tenant_id` column, RLS policies

**Rationale for RLS:**
- Schema-per-tenant requires running migrations for every new customer — operationally painful at 100+ tenants
- Database-per-tenant is prohibitively expensive and complex to operate
- RLS is the industry standard: Stripe, Linear, Notion all use this approach
- Easier to query across tenants for aggregate ML training data
- Postgres RLS is battle-tested and performant with proper indexes

**Implementation (non-negotiable):**
```sql
-- Pattern applied to ALL tenant-scoped tables
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {table}
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

-- Application layer: set at start of every request
await conn.execute("SET app.current_tenant_id = $1", [tenant_id_from_jwt])
```

**Tables with RLS:** `users`, `portfolio_suppliers`, `alerts`, `alert_rules`
**Tables WITHOUT RLS:** `suppliers`, `supplier_scores`, `supplier_ports` — these are global, not tenant-specific

**What this means for code:**
- Every FastAPI request middleware must set `app.current_tenant_id` from JWT
- Never accept `tenant_id` as a URL parameter or request body field
- Application-layer filter `WHERE tenant_id = {jwt_tenant_id}` on every query as defence-in-depth
- Tests must set `app.current_tenant_id` explicitly — never rely on RLS alone in tests

---

## ADR-007: Heuristic Scorer Before ML Model

**Status:** Accepted
**Date:** Project init

**Context:** Cannot train an ML model without labelled training data. Cannot get labelled data without a shipped product. Classic cold-start problem.

**Decision:** Ship a rules-based heuristic scorer (v0) first. Replace with XGBoost once 3 months of data and 100+ labelled disruption events are collected.

**Rationale:**
- Heuristic based on Altman Z-Score literature is academically defensible and explainable to customers
- Shipping a product generates the labels needed to train the real model (customers report disruptions)
- Heuristic v0 and XGBoost v1 produce identical output schema — drop-in replacement (see ADR-014)
- Avoids the deadlock: "need labels to train → need product to get labels → need model to ship product"

**Transition criteria (all must be met before switching to XGBoost):**
- ≥ 3 months of clean signal data in Postgres
- ≥ 100 labelled positive disruption events
- XGBoost PR-AUC ≥ 0.25 on holdout set
- SHAP values implemented and passing review

**What this means for code:**
- `ml/scoring/heuristic_scorer.py` is the v0 model — never deleted, kept as fallback
- `model_version = "heuristic_v0"` tag on all scores from this model
- The scoring runner (`ml/scoring/run_scoring.py`) accepts any class implementing the `RiskScorer` Protocol — swappable without API changes

---

## ADR-008: dbt for All SQL Transformations

**Status:** Accepted
**Date:** Project init

**Context:** How to manage SQL transformations in the pipeline schema — ad-hoc scripts vs. a transformation framework.

**Decision:** dbt Core for all transformations. No raw SQL scripts, no stored procedures, no database-native task schedulers for transformation logic.

**Rationale:**
- Version-controlled SQL: every transformation in Git with history
- Built-in testing: `not_null`, `unique`, `accepted_values`, custom SQL tests
- Documentation: schema.yml auto-generates a data catalogue
- Lineage graph: know exactly what breaks when an upstream source changes
- Idempotent: running dbt twice produces the same result — safe to retry
- Industry standard: dbt is the dominant transformation tool, large community

**Hard rule:** If it transforms pipeline data, it is a dbt model. No exceptions.
- No Python scripts that run `INSERT INTO ... SELECT` directly in the pipeline schema
- No Airflow operators that execute raw SQL against pipeline tables
- No stored procedures for business logic

**What this means for code:**
- All dbt models in `data/dbt/models/`
- Three layers: `raw/` (views over sources), `staging/` (clean + typed tables), `marts/` (business aggregates)
- Every model has a corresponding entry in `schema.yml` with column descriptions and tests
- dbt triggered by Airflow after each ingestion run (`dbt run --select staging.stg_{source}+`)

---

## ADR-009: Hybrid Entity Resolution (Rules + LLM)

**Status:** Accepted
**Date:** Project init
**Closes:** Open ADR-009 from original draft

**Context:** Company name strings from news, SEC filings, and AIS data must be matched to canonical supplier IDs. Options ranged from pure fuzzy matching to full LLM resolution.

**Decision:** Three-stage hybrid pipeline: exact match → fuzzy match → LLM-assisted for hard cases only.

**Options evaluated:**
- A. OpenCorporates API — $200/month, good coverage, still needs fuzzy matching
- B. Pure spaCy NER + RapidFuzz — free, covers 80%+ of cases, misses subsidiaries
- C. Hybrid: rules (stages 1+2) + GPT-4o-mini for hard cases (stage 3)
- D. Build own registry from DUNS + manual curation

**Rationale for hybrid (C + D elements):**
- Stage 1 (exact match after normalisation): handles ~60% of cases at zero cost
- Stage 2 (RapidFuzz token_sort_ratio ≥ 85): handles ~25% more at negligible compute cost
- Stage 3 (GPT-4o-mini): only for 70–84 fuzzy score range — ~$0.0001/call, max 200 calls/day = $0.02/day
- Total cost: essentially free vs. OpenCorporates at $200/month
- Quality: LLM substantially outperforms fuzzy matching for subsidiaries, abbreviations, and non-ASCII names

**LLM daily call limit:** 200 calls/day (env var: `LLM_RESOLUTION_DAILY_LIMIT=200`)
- If limit reached: log warning, mark as unresolved, continue
- Never block ingestion pipeline on LLM availability

**What this means for code:**
- Implementation: `data/pipeline/entity_resolution.py`
- Three-stage `EntityResolver` class with injected `SupplierRegistry` and optional `LLMClient`
- `LLMClient` is a Protocol — swappable for tests (no real API calls in tests, see ADR-013)
- Unresolved entities → `staging.unresolved_entities` table, never silently dropped
- Target resolution rate: ≥ 85% of articles with company mentions resolve to a `supplier_id`

---

## ADR-010: Repository Pattern for All Data Access

**Status:** Accepted
**Date:** Project init

**Context:** How to structure database access across the codebase — raw SQL in endpoints vs. ORM vs. repository pattern.

**Decision:** Repository pattern with Protocol interfaces for all data access. No raw SQL in endpoints, services, or ML code.

**Rationale:**
- Testability: every repository has an `InMemory` implementation — tests run without any database
- Swappability: Postgres and InMemory implementations behind the same interface — easy to add other backends later
- Separation of concerns: business logic has no knowledge of SQL syntax or database specifics
- Consistency: one place to change a query, one place to mock in tests
- Mandatory by ADR-013: no live DB calls in tests requires this pattern

**Required structure for every repository:**

```python
# 1. Protocol (interface) — defines the contract
class SupplierScoreRepository(Protocol):
    async def get_latest_score(self, supplier_id: str) -> RiskScoreOutput | None: ...
    async def upsert_daily_score(self, record: DailyScoreRecord) -> None: ...
    async def get_score_history(self, supplier_id: str, days: int) -> list[DailyScoreRecord]: ...

# 2. InMemory implementation — for tests
class InMemorySupplierScoreRepository:
    def __init__(self) -> None:
        self._scores: dict[str, DailyScoreRecord] = {}
    
    async def get_latest_score(self, supplier_id: str) -> RiskScoreOutput | None:
        return self._scores.get(supplier_id)
    # ... etc

# 3. Production implementation — Postgres
class PostgresSupplierScoreRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
    
    async def get_latest_score(self, supplier_id: str) -> RiskScoreOutput | None:
        # actual SQL here
        ...
```

**What this means for code:**
- Every file in `backend/app/repositories/` follows this three-class pattern
- FastAPI routes receive repositories via dependency injection — never instantiate directly in routes
- ML scoring code receives repository via constructor injection — never instantiate directly in run_scoring.py
- Violation of this pattern is a blocker for PR merge

---

## ADR-011: Pydantic v2 for All Data Models

**Status:** Accepted
**Date:** Project init

**Context:** Pydantic v1 and v2 have breaking API differences. Must standardise to avoid mixed syntax.

**Decision:** Pydantic v2 throughout the entire codebase. No v1 syntax.

**Key v2 differences (Claude must know these):**

```python
# ✅ Pydantic v2 syntax
from pydantic import BaseModel, field_validator, model_validator

class SupplierScore(BaseModel):
    supplier_id: str
    score: int
    
    @field_validator("score")           # v2: field_validator, not validator
    @classmethod
    def score_must_be_valid(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"Score must be 0-100, got {v}")
        return v
    
    model_config = ConfigDict(          # v2: model_config, not class Config
        frozen=True,
        str_strip_whitespace=True,
    )

# ❌ Never use v1 syntax
class OldModel(BaseModel):
    @validator("score")                 # v1 — do not use
    def validate_score(cls, v):
        ...
    
    class Config:                       # v1 — do not use
        frozen = True
```

**pydantic-settings v2 for config:**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    database_url: str
    kafka_bootstrap_servers: str
```

**What this means for code:**
- `pip install pydantic>=2.6.0 pydantic-settings>=2.1.0`
- Import from `pydantic` not `pydantic.v1`
- Use `model_dump()` not `.dict()` (v1 method, deprecated in v2)
- Use `model_validate()` not `.parse_obj()` (v1 method, deprecated in v2)
- Use `model_json_schema()` not `.schema()` (v1 method, deprecated in v2)

---

## ADR-012: Celery for Background Tasks

**Status:** Accepted
**Date:** Project init

**Context:** Need background task processing for: email alert dispatch, Slack alert dispatch, WebSocket push notifications. (Score sync is not needed — ML pipeline writes directly to Postgres.)

**Decision:** Celery with Redis as broker and result backend.

**Options evaluated:**
- A. Celery — battle-tested, rich ecosystem, Redis/RabbitMQ broker support
- B. APScheduler — simpler, but no distributed workers, no task retry UI
- C. Airflow for everything — overkill for simple notification dispatch tasks
- D. FastAPI BackgroundTasks — no retry, no distributed workers, not durable

**Rationale for Celery:**
- Distributed workers: multiple Celery workers process tasks concurrently
- Durable retries: tasks survive worker crashes (stored in Redis)
- Task routing: different queues for scoring vs. notifications vs. websocket
- Beat scheduler: periodic tasks (score sync every 6h) without a separate cron
- Flower UI: real-time task monitoring (useful for debugging alert delivery)
- Redis already in stack: no additional infrastructure for broker

**Why not APScheduler:** No distributed workers, no task durability, single process only.
**Why not Airflow for tasks:** Airflow is for DAG orchestration, not sub-second task dispatch.

**Queue structure:**
```
notifications  → send_email_alert, send_slack_alert (lightweight, latency-sensitive)
websocket      → push_ws_alert (very lightweight, real-time)
```

**What this means for code:**
- Celery app in `backend/app/celery_app.py`
- Tasks in `backend/app/tasks/` (one file per queue: notifications, websocket)
- Redis URL from `settings.redis_url` — same Redis instance as cache (different DB index)
- Celery broker: `redis://localhost:6379/0` (DB 0)
- Test Redis: `redis://localhost:6379/1` (DB 1) — isolated from production data

---

## ADR-013: No Live External Calls in Tests

**Status:** Accepted
**Date:** Project init

**Context:** Tests that call real external services (SEC EDGAR, NewsAPI, Kafka, Auth0) or a real database (Postgres) are slow, flaky, require credentials, and cannot run in CI without secrets.

**Decision:** Zero live external calls in any test. All external dependencies mocked or replaced with in-memory implementations.

**Mocking strategy by dependency type:**

```
HTTP APIs (SEC EDGAR, NewsAPI, MarineTraffic, FRED):
  → Use respx library to mock httpx calls
  → Fixture data in tests/fixtures/{source}_response.json
  → Never use unittest.mock.patch for HTTP — too brittle

Kafka:
  → Mock KafkaProducer/Consumer in conftest.py
  → Capture published messages in a list for assertion
  → Never start a real Kafka broker in unit tests

Postgres:
  → Use InMemory{Repository} implementations (ADR-010)
  → Never connect to real database in unit tests
  → Integration tests (in tests/integration/) may use local Docker Postgres

Redis:
  → fakeredis library for unit tests
  → Real Redis (Docker) for integration tests only

Auth0:
  → Mock JWT tokens with known claims in conftest.py
  → Never call Auth0 JWKS endpoint in tests

LLM (GPT-4o-mini for entity resolution):
  → Mock LLMClient Protocol with deterministic responses
  → Never call OpenAI API in tests
```

**Test speed target:** Full test suite must complete in < 60 seconds.

**What this means for code:**
- `tests/conftest.py` provides: `mock_settings`, `mock_kafka_producer`, `mock_http_client`, `mock_jwt`
- All fixtures use `pytest-asyncio` with `asyncio_mode = "auto"`
- Integration tests in `tests/integration/` — run separately, require Docker services
- Unit tests in `tests/` — run with `make test`, zero external dependencies
- CI runs unit tests only — integration tests run manually before major releases

---

## ADR-014: Frozen RiskScoreOutput Schema

**Status:** Accepted
**Date:** Project init

**Context:** The heuristic scorer (v0) and future XGBoost model (v1) must be interchangeable without API changes. Downstream consumers (Postgres sync, alert engine, frontend) must not need to change when the model changes.

**Decision:** `RiskScoreOutput` schema is frozen. The heuristic scorer and all future ML models must produce identical output structure. The ML model is a drop-in replacement.

**Frozen schema:**
```python
class SignalContribution(BaseModel):
    signal_name: str
    display_name: str
    category: Literal["financial", "news", "shipping", "geopolitical", "macro"]
    raw_value: float | None
    contribution: float             # points contributed to final score
    direction: Literal["increases_risk", "decreases_risk", "neutral"]
    explanation: str                # human-readable explanation

class RiskScoreOutput(BaseModel):
    supplier_id: str
    score: int                      # 0–100, higher = more risk
    risk_level: Literal["low", "medium", "high"]
    financial_score: float          # 0–100 for this category
    news_score: float
    shipping_score: float
    geo_score: float
    macro_score: float
    top_drivers: list[SignalContribution]   # exactly 5, sorted by abs(contribution) desc
    all_signals: list[SignalContribution]   # all signals computed
    model_version: str              # "heuristic_v0", "xgboost_v1", etc.
    feature_date: date
    scored_at: datetime
    data_completeness: float        # 0.0–1.0
```

**What "frozen" means:**
- Fields cannot be removed — downstream consumers depend on them
- Fields cannot be renamed — breaking change
- New optional fields CAN be added (with default values) — non-breaking
- If a breaking change is truly needed: create `RiskScoreOutputV2`, version the API endpoint, migrate consumers

**RiskScorer Protocol (what all models must implement):**
```python
class RiskScorer(Protocol):
    MODEL_VERSION: str
    
    def score(self, features: SupplierFeatureVector) -> RiskScoreOutput: ...
    def score_batch(self, features: list[SupplierFeatureVector]) -> list[RiskScoreOutput]: ...
```

---

## ADR-015: Conda (genai environment) for Python Environment

**Status:** Accepted
**Date:** Project init

**Context:** Need to specify Python environment management to avoid conflicts between venv and conda.

**Decision:** Use existing conda environment named `genai`. Do not create new environments or venvs.

**Rationale:**
- `genai` conda environment already exists with common ML/data libraries pre-installed
- conda manages Python version (3.11) and system-level dependencies cleanly
- Avoids conflicts between conda and venv on the same machine
- Common libraries (numpy, pandas, torch, scikit-learn) likely already installed — pip fills gaps only

**Rules for Claude Code (non-negotiable):**
- Never run `python -m venv` or `conda create`
- Never suggest creating a new environment
- Always assume `conda activate genai` is already done before running any Python command
- Use `pip install` (not `conda install`) for project-specific packages
- Check if a package is installed before adding to requirements.txt: `pip show {package}`
- Pin all packages in requirements.txt with exact versions (`==`) not ranges (`>=`)

**Environment activation:**
```bash
conda activate genai
pip install -r requirements.txt -r requirements-dev.txt  # fills any gaps
```

---

## ADR-016: FinBERT for News Sentiment with Lexicon Fallback

**Status:** Accepted
**Date:** Project init

**Context:** Need sentiment scoring for news articles. Options range from simple keyword matching to large language models.

**Decision:** ProsusAI/finbert as primary sentiment model. Lexicon-based scorer as automatic fallback.

**Options evaluated:**
- A. VADER — fast, free, not domain-specific, poor on financial text
- B. TextBlob — simple, inaccurate on financial language
- C. FinBERT (ProsusAI/finbert) — fine-tuned on financial news, 3-class output
- D. GPT-4o for sentiment — high quality, expensive (~$0.01/article = $50/day at scale)
- E. Fine-tuned custom model — best quality, requires 10K+ labelled examples we don't have

**Rationale for FinBERT:**
- Fine-tuned on financial news corpus — dramatically better than VADER/TextBlob on supply chain language
- 3-class output (positive/negative/neutral) with probability scores
- Runs locally — no API cost, no external dependency
- ONNX export available for fast CPU inference (no GPU required in production)
- Widely used in financial NLP — well-documented failure modes

**Fallback rules:**
- If FinBERT model file not found: use lexicon scorer automatically, log warning
- If GPU OOM: reduce batch size to 8, retry; if still fails, use lexicon scorer
- If inference time > 5 seconds per article: use lexicon scorer (signals pipeline backup)
- Tag all records with `sentiment_model = 'finbert'` or `sentiment_model = 'lexicon_fallback'`
- Never block ingestion pipeline on model availability

**Model serving:**
- Load model once at worker startup — not per article
- Batch inference: 32 articles per batch (optimal for FinBERT on CPU)
- ONNX export preferred for production: `optimum-cli export onnx --model ProsusAI/finbert`

---

## How to Add a New ADR

Copy this template:

```markdown
## ADR-{next_number}: {Short Decision Title}

**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-{n}
**Date:** {date}
**Supersedes:** ADR-{n} (if applicable)

**Context:**
{What problem are we solving? What forces are at play?}

**Decision:**
{What did we decide?}

**Options evaluated:**
- A. {option} — {brief pros/cons}
- B. {option} — {brief pros/cons}

**Rationale:**
{Why this option over the others?}

**Trade-offs:**
{What are we giving up? When should we revisit?}

**What this means for code:**
{Concrete implications for implementation — what to do and not do}
```

**Rules:**
- Never modify an Accepted ADR — add a superseding ADR
- "Supersedes" field must reference the old ADR
- Every ADR must have "What this means for code" — abstract decisions without implementation guidance are useless
- Status must be one of: Proposed, Accepted, Deprecated, Superseded
- Add to the ADR Index table at the top when accepted

---

*Last updated: Project init — all ADRs current as of Session 1 start.*
