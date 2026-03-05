# ARCHITECTURE.md — System Design

> Read this before making ANY infrastructure, database, or system-level decision.
> If something is not specified here, check DECISIONS.md before inventing an answer.
> If still not covered, stop and ask — do not guess.

---

## 1. System Overview

The platform has five logical layers. Each layer has a single responsibility and communicates with adjacent layers through well-defined interfaces. No layer skips another.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1: DATA INGESTION                                         │
│  SEC EDGAR · NewsAPI · GDELT · MarineTraffic · FRED · OFAC      │
│  NOAA Weather · ACLED Conflict                                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │ raw events → Kafka topics (raw.*)
                       │ raw files  → S3 (immutable archive)
┌──────────────────────▼──────────────────────────────────────────┐
│  LAYER 2: DATA PIPELINE                                          │
│  Kafka Consumers → NLP Processor → Postgres (pipeline schema)    │
│  dbt transforms: raw → staging → marts                           │
└──────────────────────┬──────────────────────────────────────────┘
                       │ supplier_feature_vector (Postgres pipeline table)
┌──────────────────────▼──────────────────────────────────────────┐
│  LAYER 3: ML & SCORING ENGINE                                    │
│  Heuristic v0 → XGBoost · FinBERT · SHAP                        │
│  MLflow Model Registry · Drift Detection                         │
└──────────────────────┬──────────────────────────────────────────┘
                       │ DailyScoreRecord → Postgres pipeline schema
                       │ scores.updated   → Kafka topic
┌──────────────────────▼──────────────────────────────────────────┐
│  LAYER 4: BACKEND SERVICES                                       │
│  FastAPI · Alert Engine · Auth0 · Redis · Celery · Postgres      │
│  Scoring writes directly to Postgres · API reads from Postgres   │
└──────────────────────┬──────────────────────────────────────────┘
                       │ REST API + WebSocket
┌──────────────────────▼──────────────────────────────────────────┐
│  LAYER 5: PRODUCT                                                │
│  React Dashboard · Alert Centre · Risk Map · Mobile Alerts       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Environment Architecture

**This is critical.** Local dev and production use different implementations of the same interfaces. Claude Code must never make live AWS calls during development.

```
                    LOCAL DEV                    PRODUCTION
                    ─────────                    ──────────
Kafka         →     bitnami/kafka (Docker)        AWS MSK (3 brokers)
Postgres      →     postgres:15 (Docker)          AWS RDS Multi-AZ
Redis         →     redis:7 (Docker)              AWS ElastiCache
S3            →     localstack (Docker) OR skip   AWS S3 (versioned)
Airflow       →     LocalExecutor (Docker)         CeleryExecutor (EKS)
ML models     →     local file (./ml/models/)     MLflow on S3
Auth          →     mock JWT in tests             Auth0
Email         →     MailHog (Docker :8025)        SendGrid

Rule: If a service requires cloud credentials to run, it must have
a local/mock alternative. Tests must NEVER require cloud credentials.
```

---

## 3. Detailed Data Flow

### 3.1 Ingestion → Pipeline

```
Ingestion Script (Airflow DAG)
  │
  ├─► S3 raw archive (FIRST — immutable backup before any processing)
  │     Path: s3://srip-data-lake/raw/{source}/{year}/{month}/{day}/{uuid}.json
  │     Never modified after write. Versioned bucket.
  │
  └─► Kafka topic raw.{source}
        │
        ├─► Kafka Consumer (per topic)
        │     - Deserialise JSON
        │     - Validate against Pydantic schema
        │     - On valid:   write to Postgres pipeline schema
        │     - On invalid: write to raw.dlq.{source} + structured log
        │
        └─► Postgres pipeline.raw_{source}
              │
              └─► dbt runs (triggered by Airflow after each ingest)
                    raw → staging (clean, type, deduplicate)
                    staging → marts (join, aggregate, compute features)
                    marts → supplier_feature_vector (ML input)
```

### 3.2 Pipeline → ML Scoring

```
Airflow DAG: ml_score_suppliers (every 6h)
  │
  ├─► Read supplier_feature_vector from Postgres pipeline schema
  │     Query: all suppliers with feature_date = today
  │     Batch size: 500 suppliers per batch
  │
  ├─► Load model from MLflow registry (production stage)
  │     Local dev: load from ./ml/models/current_model.pkl
  │     Production: load from s3://srip-mlflow-artifacts/
  │
  ├─► Score each supplier → RiskScoreOutput (Pydantic model)
  │     Compute SHAP values for every score
  │
  ├─► Write DailyScoreRecord to Postgres pipeline.supplier_daily_scores
  │     Upsert on (supplier_id, feature_date)
  │
  └─► Publish to Kafka scores.updated
        Message: {supplier_id, score, previous_score, scored_at}
        Consumer: Alert Engine
```

### 3.3 ML → Backend (Score Access)

```
ML scoring pipeline writes DailyScoreRecord directly to Postgres pipeline schema.
Backend API reads scores from the same Postgres database. No sync step required.

After scoring run:
  ├─► Update Redis cache for any supplier_id that changed
  │     Key: score:{supplier_id}
  │     Value: JSON of latest DailyScoreRecord
  │     TTL: 4 hours (refreshed on next scoring run)
  │
  └─► API reads from Postgres supplier_scores table (or Redis cache)
        Cache miss → Postgres query → cache result (TTL: 4h)
```

### 3.4 Alert Engine Flow

```
Kafka Consumer: scores.updated topic
  │
  ├─► Load alert rules for all tenants who monitor this supplier
  │     Query: SELECT ar.* FROM alert_rules ar
  │            JOIN portfolio_suppliers ps ON ps.tenant_id = ar.tenant_id
  │            WHERE ps.supplier_id = {supplier_id}
  │
  ├─► Evaluate each rule:
  │     score_spike:     score - previous_score >= threshold (default 15)
  │     high_threshold:  score >= threshold (default 70)
  │     sanctions_hit:   on_sanctions_list = True (immediate, no threshold)
  │
  ├─► For each triggered rule → write Alert to Postgres alerts table
  │
  └─► Publish to Kafka alerts.dispatch
        │
        ├─► Email worker (Celery) → SendGrid (prod) / MailHog (dev)
        ├─► Slack worker (Celery) → Slack webhook if configured
        └─► WebSocket push → Redis pub/sub → connected React clients
```

### 3.5 Backend → Frontend

```
React App loads:
  1. GET /api/v1/portfolio/suppliers
     → FastAPI reads from Postgres (pipeline schema — same DB, no sync needed)
     → Redis cache checked first (key: portfolio:{tenant_id})
     → Cache miss → Postgres query → cache result (TTL: 5 min)

  2. WebSocket ws://api/ws/alerts?token={jwt}
     → Subscribe to Redis channel: alerts:{tenant_id}
     → Push alert objects as they arrive from Kafka consumer

  3. GET /api/v1/map/suppliers
     → Returns GeoJSON FeatureCollection
     → One feature per supplier with coordinates + risk score properties
     → Coordinates from suppliers.primary_location (lat/lng)
```

---

## 4. Postgres Schema (Operational Database)

This is the API database — the warm copy served to users. Not the analytics store.

```sql
-- ─────────────────────────────────────────────
-- TENANTS
-- ─────────────────────────────────────────────
CREATE TABLE tenants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(255) NOT NULL,
    plan                VARCHAR(50) NOT NULL CHECK (plan IN ('starter', 'growth', 'pro', 'enterprise')),
    stripe_customer_id  VARCHAR(255) UNIQUE,
    max_suppliers       INTEGER NOT NULL DEFAULT 25,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- USERS
-- ─────────────────────────────────────────────
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email       VARCHAR(255) NOT NULL,
    role        VARCHAR(50) NOT NULL CHECK (role IN ('admin', 'viewer')),
    auth0_id    VARCHAR(255) UNIQUE NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_users_tenant_email ON users(tenant_id, email);
CREATE INDEX idx_users_auth0_id ON users(auth0_id);

-- ─────────────────────────────────────────────
-- CANONICAL SUPPLIER REGISTRY
-- ─────────────────────────────────────────────
CREATE TABLE suppliers (
    id                  VARCHAR(30) PRIMARY KEY,   -- prefix 'sup_', e.g. sup_01HX...
    canonical_name      VARCHAR(500) NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    country             CHAR(2) NOT NULL,           -- ISO 3166-1 alpha-2
    industry_code       VARCHAR(10),                -- NAICS code
    industry_name       VARCHAR(255),
    duns_number         VARCHAR(9),
    cik                 VARCHAR(10),                -- SEC CIK if public company
    website             VARCHAR(500),
    primary_location    JSONB,                      -- {lat: float, lng: float, city: str, country: str}
    primary_port_id     VARCHAR(50),                -- MarineTraffic port ID
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_suppliers_country ON suppliers(country);
CREATE INDEX idx_suppliers_cik ON suppliers(cik) WHERE cik IS NOT NULL;
CREATE INDEX idx_suppliers_canonical_name ON suppliers USING gin(to_tsvector('english', canonical_name));

-- ─────────────────────────────────────────────
-- TENANT PORTFOLIOS
-- ─────────────────────────────────────────────
CREATE TABLE portfolio_suppliers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    supplier_id     VARCHAR(30) NOT NULL REFERENCES suppliers(id),
    internal_id     VARCHAR(255),                   -- customer's own vendor ID
    custom_name     VARCHAR(255),                   -- customer's name for this supplier
    tags            TEXT[] NOT NULL DEFAULT '{}',
    added_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_portfolio_tenant_supplier ON portfolio_suppliers(tenant_id, supplier_id);
CREATE INDEX idx_portfolio_tenant_id ON portfolio_suppliers(tenant_id);

-- ─────────────────────────────────────────────
-- RISK SCORES
-- ─────────────────────────────────────────────
CREATE TABLE supplier_scores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id         VARCHAR(30) NOT NULL REFERENCES suppliers(id),
    score               SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    risk_level          VARCHAR(10) NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    score_date          DATE NOT NULL,
    signal_breakdown    JSONB NOT NULL,             -- full RiskScoreOutput as JSON
    model_version       VARCHAR(50) NOT NULL,
    data_completeness   NUMERIC(3,2),               -- 0.00 to 1.00
    synced_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_scores_supplier_date ON supplier_scores(supplier_id, score_date);
CREATE INDEX idx_scores_supplier_id ON supplier_scores(supplier_id);
CREATE INDEX idx_scores_score_date ON supplier_scores(score_date DESC);
-- Partial index for high-risk suppliers (frequent access pattern)
CREATE INDEX idx_scores_high_risk ON supplier_scores(supplier_id, score_date DESC)
    WHERE score >= 70;

-- ─────────────────────────────────────────────
-- ALERTS
-- ─────────────────────────────────────────────
CREATE TABLE alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    supplier_id     VARCHAR(30) NOT NULL REFERENCES suppliers(id),
    alert_type      VARCHAR(50) NOT NULL CHECK (
                        alert_type IN ('score_spike', 'high_threshold', 'event_detected', 'sanctions_hit')
                    ),
    severity        VARCHAR(10) NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    title           VARCHAR(500) NOT NULL,
    message         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',    -- score before/after, signals triggered, etc.
    status          VARCHAR(20) NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'investigating', 'resolved', 'dismissed')),
    note            TEXT,                           -- user-added investigation note
    fired_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMP WITH TIME ZONE,
    resolved_at     TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_alerts_tenant_status ON alerts(tenant_id, status, fired_at DESC);
CREATE INDEX idx_alerts_supplier_id ON alerts(supplier_id);
CREATE INDEX idx_alerts_fired_at ON alerts(fired_at DESC);

-- ─────────────────────────────────────────────
-- ALERT RULES (per-tenant configuration)
-- ─────────────────────────────────────────────
CREATE TABLE alert_rules (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rule_name               VARCHAR(255) NOT NULL,
    score_spike_threshold   SMALLINT NOT NULL DEFAULT 15 CHECK (score_spike_threshold BETWEEN 5 AND 50),
    high_risk_threshold     SMALLINT NOT NULL DEFAULT 70 CHECK (high_risk_threshold BETWEEN 50 AND 95),
    channels                JSONB NOT NULL DEFAULT '{"email": {"enabled": true}, "slack": {"enabled": false}}',
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_alert_rules_tenant ON alert_rules(tenant_id);

-- ─────────────────────────────────────────────
-- INGESTION LOG (for monitoring pipeline health)
-- ─────────────────────────────────────────────
CREATE TABLE ingestion_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(50) NOT NULL,           -- 'sec_edgar', 'news', 'ais', etc.
    dag_run_id      VARCHAR(255),
    run_date        DATE NOT NULL,
    records_fetched INTEGER NOT NULL DEFAULT 0,
    records_written INTEGER NOT NULL DEFAULT 0,
    records_failed  INTEGER NOT NULL DEFAULT 0,
    duration_seconds NUMERIC(10,2),
    status          VARCHAR(20) NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    error_message   TEXT,
    started_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_ingestion_log_source_date ON ingestion_log(source, run_date DESC);

-- ─────────────────────────────────────────────
-- ROW LEVEL SECURITY
-- ─────────────────────────────────────────────
-- Enable RLS on all tenant-scoped tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_suppliers ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_rules ENABLE ROW LEVEL SECURITY;

-- Policy: users can only see their own tenant's data
-- Application sets app.current_tenant_id at start of each request
CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation ON portfolio_suppliers
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation ON alerts
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
CREATE POLICY tenant_isolation ON alert_rules
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

---

## 5. Postgres Pipeline Schema

dbt runs against Postgres. All pipeline tables live in the `pipeline` schema,
separate from the operational (`public`) schema used by the backend API.

Production: migrate to Snowflake or BigQuery when Postgres query performance degrades.

```
RAW tables — immutable, written directly by ingestion consumers
  pipeline.raw_sec_filings        (cik, company_name, filing_type, filed_date,
                                   period_of_report, financials JSONB,
                                   going_concern BOOLEAN, ingested_at TIMESTAMPTZ)

  pipeline.raw_news_articles      (article_id, url, title, content, published_at,
                                   source_name, ingestion_source, ingested_at TIMESTAMPTZ)

  pipeline.raw_ais_events         (port_id, port_name, vessel_mmsi, vessel_name,
                                   arrival_time, departure_time, cargo_type,
                                   ingested_at TIMESTAMPTZ)

  pipeline.raw_macro_series       (series_id, series_name, observation_date,
                                   value FLOAT, unit, ingested_at TIMESTAMPTZ)

  pipeline.raw_geo_events         (event_id, event_type, country, region,
                                   event_date, severity, source, ingested_at TIMESTAMPTZ)

  pipeline.raw_supplier_registry  (supplier_id, canonical_name, aliases TEXT[],
                                   country, industry_code, duns_number, cik,
                                   primary_port_id, created_at, updated_at)

STAGING tables — cleaned, typed, deduplicated by dbt
  pipeline.stg_sec_financials     (one row per cik + period_end, deduplicated)
  pipeline.stg_news_sentiment     (one row per article_id, with NLP enrichment)
  pipeline.stg_shipping_volume    (one row per port_id + day, aggregated)
  pipeline.stg_geo_risk           (one row per country + date)
  pipeline.stg_macro_indicators   (one row per series_id + observation_date)

MART tables — business-level features, joined to supplier_id by dbt
  pipeline.mart_dim_suppliers              (supplier dimension table)
  pipeline.mart_supplier_financial_features (Altman Z, ratios, trends — per supplier per quarter)
  pipeline.mart_supplier_news_features     (sentiment scores, topic flags — per supplier per day)
  pipeline.mart_supplier_shipping_features (volume delta, dwell time — per supplier per day)
  pipeline.mart_supplier_geo_features      (country risk, sanctions — per supplier per day)
  pipeline.mart_supplier_macro_features    (commodity prices, PMI — per supplier per day)
  pipeline.supplier_feature_vector         (JOIN of all above — ML input, one row per supplier per day)

SCORES tables — ML pipeline outputs
  pipeline.supplier_daily_scores  (supplier_id, score, risk_level, score_date,
                                   signal_breakdown JSONB, model_version,
                                   data_completeness, scored_at TIMESTAMPTZ)
                                   UNIQUE on (supplier_id, score_date)

  pipeline.signal_shap_values     (supplier_id, score_date, signal_name,
                                   contribution FLOAT, direction, raw_value,
                                   explanation)

  pipeline.model_metadata         (model_version, model_type, trained_at,
                                   training_auc, training_pr_auc,
                                   feature_importance JSONB,
                                   deployed_at, is_active)
```

---

## 6. Feature Store

The Feature Store is **not a separate service**. It is the `pipeline.supplier_feature_vector` table in Postgres.

```
Feature Store = Postgres pipeline.supplier_feature_vector

Schema:
  supplier_id             VARCHAR   -- canonical supplier ID
  feature_date            DATE      -- date features were computed for
  feature_vector_created_at TIMESTAMPTZ
  [all 30+ feature columns from ML_SPEC.md]

Access pattern:
  - Written by: dbt (after each ingestion run)
  - Read by: ml/scoring/run_scoring.py (batch read, all suppliers, today's date)
  - Batch size: 500 rows per read to avoid memory issues

Local dev substitute:
  - InMemoryFeatureStore (for unit tests)
  - CSV fixture file at tests/fixtures/feature_vectors.csv (for integration tests)
  - Never call the database directly in unit tests — use InMemory repositories
```

---

## 7. S3 Data Lake Structure

```
s3://srip-data-lake-{env}/
  raw/
    sec_edgar/
      2025/03/04/
        {uuid}.json          ← one file per filing batch run
    news/
      2025/03/04/14/         ← hourly partitioned
        {uuid}.json
    ais/
      2025/03/04/
        {uuid}.json
    macro/
      2025/03/04/
        {uuid}.json
    geo/
      2025/03/04/
        {uuid}.json
  exports/
    {tenant_id}/
      reports/
        {date}-weekly-report.pdf
  mlflow/
    artifacts/
      {experiment_id}/
        {run_id}/
          model/
          shap_summary.png
          feature_importance.json

Naming rules:
  - UUID v4 for all file names (never timestamps — avoids collisions)
  - Never overwrite an existing file — always new UUID
  - Bucket versioning enabled — accidental overwrites are recoverable
```

---

## 8. Kafka Topics (Full Specification)

| Topic | Producer | Consumer | Partitions | Retention | Message Schema |
|---|---|---|---|---|---|
| `raw.sec` | SEC scraper | ETL worker | 3 | 7 days | `SECRawEvent` |
| `raw.news` | News ingestion | NLP worker | 6 | 7 days | `NewsRawEvent` |
| `raw.ais` | AIS consumer | ETL worker | 3 | 3 days | `AISRawEvent` |
| `raw.macro` | FRED poller | ETL worker | 1 | 30 days | `MacroRawEvent` |
| `raw.geo` | Geo feed | ETL worker | 1 | 7 days | `GeoRawEvent` |
| `raw.dlq.sec` | ETL worker | Monitoring | 1 | 30 days | `DeadLetterEvent` |
| `raw.dlq.news` | NLP worker | Monitoring | 1 | 30 days | `DeadLetterEvent` |
| `raw.dlq.ais` | ETL worker | Monitoring | 1 | 30 days | `DeadLetterEvent` |
| `scores.updated` | Scoring job | Alert engine | 3 | 24h | `ScoreUpdatedEvent` |
| `alerts.dispatch` | Alert engine | Email/Slack workers | 3 | 24h | `AlertDispatchEvent` |

**Dead Letter Queue (DLQ) message schema:**
```python
class DeadLetterEvent(BaseModel):
    original_topic: str
    original_payload: dict          # raw message that failed
    error_type: str                 # exception class name
    error_message: str
    failed_at: datetime
    retry_count: int
    source: str                     # which ingestion source
```

**Kafka configuration (local docker-compose):**
```yaml
KAFKA_CFG_AUTO_CREATE_TOPICS_ENABLE: "true"
KAFKA_CFG_DEFAULT_REPLICATION_FACTOR: "1"   # 1 for local, 3 for prod
KAFKA_CFG_NUM_PARTITIONS: "3"
```

---

## 9. Airflow DAGs

| DAG ID | Schedule | Trigger | Downstream |
|---|---|---|---|
| `ingest_sec_edgar` | `0 2 * * *` | Time | `dbt_transform` |
| `ingest_news` | `0 */2 * * *` | Time | `dbt_transform` |
| `ingest_ais` | `0 */4 * * *` | Time | `dbt_transform` |
| `ingest_macro` | `0 6 * * *` | Time | `dbt_transform` |
| `ingest_geo` | `0 */6 * * *` | Time | `dbt_transform` |
| `dbt_transform` | Triggered | Sensor on upstream | `ml_score_suppliers` |
| `ml_score_suppliers` | `0 */6 * * *` | Time | — |

**DAG failure handling:**
```
On task failure:
  1. Retry up to 3 times with exponential backoff (5min, 10min, 20min)
  2. On final failure: log structured error to structlog
  3. Write failure record to ingestion_log table (status='failed')
  4. Do NOT send email alerts in Phase 1 — that's Phase 3
  5. Do NOT fail downstream DAGs — each DAG is independent

On partial success (some records failed, some succeeded):
  - Write status='partial' to ingestion_log
  - Failed records go to DLQ Kafka topic
  - Continue — partial data is better than no data
```

---

## 10. Redis Cache Strategy

```
Key patterns:
  score:{supplier_id}              → Latest DailyScoreRecord JSON       TTL: 4h
  portfolio:{tenant_id}            → List of portfolio supplier IDs      TTL: 5min
  alerts:{tenant_id}:unread_count  → Integer count of unread alerts      TTL: 1min
  ws:sessions:{tenant_id}          → Set of active WebSocket session IDs TTL: 30min

Cache invalidation:
  score:{supplier_id}     → invalidated when scoring pipeline writes new score
  portfolio:{tenant_id}   → invalidated when supplier added/removed
  alerts:*                → invalidated when alert status changes

Cache warming:
  On scoring run: pre-populate score:{supplier_id} for all
  suppliers in any active portfolio (not all suppliers globally)

Local dev:
  Redis runs in docker-compose. Same behaviour as production.
  No mocking needed — Redis is fast and reliable enough for tests.
  Use a separate DB index for tests: redis://localhost:6379/1
  Production uses: redis://localhost:6379/0
```

---

## 11. Celery Configuration

```python
# backend/app/celery_config.py

CELERY_BROKER_URL = settings.redis_url          # redis://localhost:6379/0
CELERY_RESULT_BACKEND = settings.redis_url

CELERY_TASK_ROUTES = {
    "backend.tasks.send_email_alert":   {"queue": "notifications"},
    "backend.tasks.send_slack_alert":   {"queue": "notifications"},
    "backend.tasks.push_ws_alert":      {"queue": "websocket"},
}

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# Retry policy for notification tasks
CELERY_TASK_MAX_RETRIES = 3
CELERY_TASK_DEFAULT_RETRY_DELAY = 60   # seconds

# Beat schedule (periodic tasks)
# Score sync removed — ML pipeline writes directly to Postgres
CELERYBEAT_SCHEDULE = {}  # add periodic tasks here as needed
```

**Celery workers (local docker-compose):**
```bash
# Start all workers
celery -A backend.app.celery worker --loglevel=info -Q scoring,notifications,websocket

# In docker-compose, run as separate service
# Production: autoscaling worker pods on Kubernetes
```

---

## 12. Error Handling & Failure Modes

Every layer must handle failures gracefully. Never let one bad record stop a pipeline.

### Ingestion Layer Failures

| Failure | Response | Recovery |
|---|---|---|
| SEC EDGAR API down (503) | Retry 3x exponential backoff, then skip day's run | Next day's run catches up (SEC keeps filings available) |
| NewsAPI quota exceeded | Switch to GDELT fallback automatically | Resume NewsAPI next quota reset |
| MarineTraffic API timeout | Log warning, skip this run, continue | Next scheduled run (4h later) |
| Kafka broker down | Buffer in memory up to 1000 messages, retry connection | Replay from S3 archive if Kafka recovers with data loss |
| Pydantic validation fails | Route to DLQ, log structured error, continue | Manual review of DLQ |
| S3 write fails | Log error, continue (Kafka message is source of truth) | Replay from Kafka to S3 later |

### Pipeline Layer Failures

| Failure | Response | Recovery |
|---|---|---|
| dbt model fails | Log error, mark DAG as partial, continue other models | Fix model, rerun dbt for affected date |
| Postgres connection drops | Retry 3x, then fail task (triggers Airflow retry) | Airflow retries with backoff |
| NLP model OOM | Log error, use lexicon fallback, continue | Reduce batch size in config |
| Entity resolution fails | Mark supplier_id as NULL, write to unresolved_entities | Manual review queue |

### Scoring Layer Failures

| Failure | Response | Recovery |
|---|---|---|
| Feature vector missing for supplier | Score with available signals, set data_completeness < 1.0 | Missing data is flagged, not blocked |
| MLflow model unreachable | Fall back to heuristic scorer (always available locally) | Resolved when MLflow is accessible |
| Score out of range (0–100) | Clamp to [0, 100], log warning | Indicates model calibration issue — review |

### Backend Layer Failures

| Failure | Response | Recovery |
|---|---|---|
| Redis down | Fall through to Postgres directly (slower but correct) | Redis auto-recovers |
| Alert dispatch fails | Retry 3x via Celery, then mark alert as dispatch_failed | Celery retry queue |
| Auth0 unreachable | Return 503, never serve unauthenticated data | Auth0 SLA is 99.99% |

---

## 13. Data Volume Estimates

These inform batch sizes, index choices, and partition counts.

| Entity | MVP (month 6) | Year 1 | Year 2 |
|---|---|---|---|
| Tenants | 50 | 500 | 2,000 |
| Suppliers (canonical) | 10,000 | 50,000 | 200,000 |
| Suppliers monitored | 5,000 | 40,000 | 150,000 |
| SEC filings/day | ~500 | ~500 | ~500 |
| News articles/day | ~5,000 | ~20,000 | ~50,000 |
| AIS events/day | ~50,000 | ~200,000 | ~500,000 |
| Score records/day | ~5,000 | ~40,000 | ~150,000 |
| Alerts/day | ~200 | ~2,000 | ~8,000 |
| Postgres DB size | ~10GB | ~100GB | ~500GB |

**Implications for design:**
- Postgres indexes are sized for Year 1 (100GB range) — reviewed at Year 2
- Kafka partitions set to 3 for MVP — increase to 12 at Year 1 scale
- Celery worker count: 2 workers MVP, autoscale at Year 1
- Score batch size: 500 suppliers per batch (fits in 512MB RAM comfortably)
- News NLP batch size: 32 articles per GPU batch (FinBERT optimal)

---

## 14. Security Architecture

```
Secrets management:
  Local dev:   .env file (never committed, gitignored)
  Production:  AWS Secrets Manager (rotated every 90 days)
  CI/CD:       GitHub Actions secrets

Authentication flow:
  1. User logs in via Auth0 (handles passwords, SSO, MFA)
  2. Auth0 issues JWT with claims: sub, tenant_id, role, email
  3. Every FastAPI request validates JWT against Auth0 JWKS endpoint
  4. FastAPI middleware extracts tenant_id, sets app.current_tenant_id in Postgres session
  5. Row Level Security enforces tenant isolation at DB layer (defence in depth)

Network security:
  TLS 1.3 on all external connections (API, Kafka, Redis in prod)
  VPC isolation: Kafka, Postgres, Redis not publicly accessible
  API Gateway rate limiting: 1000 req/min per tenant_id

Audit logging (SOC 2 prep):
  Every data access logged: tenant_id, user_id, resource, action, timestamp
  Logs shipped to Datadog, retained 1 year
  Never log: passwords, API keys, JWT tokens, PII
```

---

## 15. Multi-Tenancy Model

```
Isolation level by layer:

Postgres:     Row Level Security on all tenant tables
              tenant_id column on: users, portfolio_suppliers, alerts, alert_rules
              Scores table is NOT tenant-scoped (scores are per supplier, not per tenant)
              Portfolio membership (portfolio_suppliers) IS tenant-scoped
              Pipeline schema tables are global — no tenant isolation needed
              Supplier signals are public data, not per-tenant

Kafka:        Single cluster, shared topics
              Messages are not tenant-tagged (supplier signals are global)
              Alert dispatch messages include tenant_id for routing

Redis:        Key prefixes include tenant_id where relevant
              score:{supplier_id} — shared (not tenant-specific)
              portfolio:{tenant_id} — tenant-specific
              alerts:{tenant_id}:* — tenant-specific

S3:           Tenant-scoped only for exported files (reports, CSV exports)
              Path: s3://srip-data-lake/exports/{tenant_id}/
              Raw data is global (not per-tenant)

API:          tenant_id always from JWT — never from request body or URL params
              Every endpoint implicitly scoped to requesting tenant
```

---

## 16. Production Infrastructure (AWS)

```
EKS Cluster (us-east-1):
  Node groups:
    api-nodes:      t3.medium × 3 (min) → × 10 (max), HPA on CPU 70%
    worker-nodes:   t3.large × 2 (min) → × 8 (max), HPA on queue depth
    airflow-nodes:  t3.large × 2 (fixed, no autoscale)

  Deployments:
    backend-api:          3 replicas, rolling update, readiness probe on /health
    celery-scoring:       2 replicas, autoscale on Redis queue depth
    celery-notifications: 2 replicas, autoscale on Redis queue depth
    airflow-scheduler:    1 replica (singleton)
    airflow-workers:      2 replicas (min), autoscale

Managed services:
  RDS Postgres 15:    db.r6g.large, Multi-AZ, automated backups 7 days
  ElastiCache Redis:  cache.r6g.large, cluster mode disabled (MVP)
  MSK Kafka:          kafka.m5.large × 3 brokers, 3 AZs
  S3:                 Versioned, lifecycle policy (archive after 90 days)

Monitoring:
  Datadog APM:        All FastAPI routes instrumented, p50/p95/p99 latency
  Datadog Logs:       All structlog output, 30-day retention
  Sentry:             Exception tracking for backend + frontend
  Grafana:            ML model metrics (score distributions, drift alerts)
  CloudWatch:         AWS resource metrics, billing alerts
```

---

*See DECISIONS.md for why specific technologies were chosen over alternatives.*
*See DATA_SOURCES.md for ingestion-specific schemas and API details.*
*See ML_SPEC.md for feature definitions and model architecture.*
*Note: pipeline schema (Section 5) targets Postgres for the prototype. Migrate to Snowflake or BigQuery when Postgres query performance degrades at scale.*
