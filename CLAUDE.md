# CLAUDE.md — Supplier Risk Intelligence Platform

> This file is read by Claude Code at the start of every session.
> It is the single source of truth for project context, conventions, and working rules.
> Do not remove or abbreviate sections. Keep it updated as the project evolves.

---

## 1. What We Are Building

A **mid-market SaaS** that gives procurement managers a real-time early warning system for supplier health deterioration — before it becomes a disruption.

**Core insight:** Resilinc and Everstream serve Fortune 500 at $50K–$200K/year. Nobody serves the $10M–$500M manufacturer. We do, at $299–$999/month, self-serve, no implementation fee.

**The product does three things:**
1. Monitors public signals for every supplier in a customer's portfolio (SEC, news, shipping, geopolitical, weather, macroeconomic)
2. Scores each supplier 0–100 using an ML ensemble (XGBoost + FinBERT NLP + SHAP explainability)
3. Alerts the procurement team when a score deteriorates — weeks before the disruption hits

---

## 2. Project Structure

```
supplier-risk-platform/
├── CLAUDE.md                  ← YOU ARE HERE (read every session)
├── docs/
│   ├── ARCHITECTURE.md        ← System design, data flow, infrastructure
│   ├── DATA_SOURCES.md        ← Every data source: schema, ingestion, refresh rate
│   └── DECISIONS.md           ← Architecture Decision Records (ADRs)
├── specs/
│   ├── PRODUCT_SPEC.md        ← Feature specs, user stories, acceptance criteria
│   ├── ML_SPEC.md             ← Model spec: features, training, evaluation, retraining
│   └── API_SPEC.md            ← REST API contracts, request/response schemas
├── prompts/
│   ├── FIRST_PROMPT.md        ← Session 1: scaffold + SEC EDGAR pipeline
│   ├── SESSION_2.md           ← Session 2: news ingestion + NLP
│   ├── SESSION_3.md           ← Session 3: entity resolution pipeline
│   ├── SESSION_4.md           ← Session 4: dbt models
│   └── SESSION_5.md           ← Session 5: heuristic scorer v0
├── backend/                   ← FastAPI Python backend (created in Phase 1)
├── frontend/                  ← React TypeScript frontend (created in Phase 3)
├── ml/                        ← ML pipeline: features, training, serving (Phase 2)
├── data/                      ← Ingestion scripts, ETL, Airflow DAGs (Phase 1)
├── infra/                     ← Terraform, Kubernetes configs (Phase 4)
└── tests/                     ← All tests live here, mirroring src structure
```

---

## 3. Current Build Phase

**PHASE 1 — Data Foundation** (Active)

Goal: Get clean, reliable data flowing before writing a single line of ML or product code.

- [ ] SEC EDGAR scraper and parser
- [ ] News API ingestion pipeline
- [ ] Supplier entity resolution (company name → canonical ID)
- [ ] Kafka event streaming setup
- [ ] Postgres schema + dbt models
- [ ] Airflow DAGs for orchestration

Do not start Phase 2 (ML) until Phase 1 data pipelines have 2 weeks of clean data in the warehouse.

---

## 4. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend API | FastAPI (Python 3.11) | Async, typed, fast to build |
| ML Pipeline | Python, XGBoost, HuggingFace, MLflow | Industry standard ML stack |
| Data Store | Postgres 15 | Single DB for prototype — migrate to warehouse at scale |
| Transformations | dbt | SQL-first, testable |
| Orchestration | Apache Airflow | DAG-based, proven |
| Event streaming | Apache Kafka | Event-driven alerting |
| Cache | Redis | Hot scores, sessions |
| Frontend | React + TypeScript + Tailwind | Type-safe, component-driven |
| Auth | Auth0 (JWT + OAuth2) | Don't build auth from scratch |
| Infra | AWS, Kubernetes, Terraform | IaC from day one |
| Monitoring | Datadog + Sentry | Errors + performance |

---

## 5. Coding Conventions

### General
- **Type everything.** Python: use type hints on all functions. TypeScript: no `any`.
- **Test first where practical.** Write the test, then the implementation.
- **Small functions.** No function longer than 40 lines. Extract aggressively.
- **Explicit over implicit.** Name variables for what they represent in the domain (`supplier_risk_score`, not `score` or `s`).
- **No magic numbers.** All constants go in `config.py` or `constants.ts`.

### Python
```python
# ✅ Do this
def compute_altman_z_score(
    working_capital: float,
    total_assets: float,
    retained_earnings: float,
    ebit: float,
    market_cap: float,
    total_liabilities: float,
    revenue: float,
) -> float:
    """Compute Altman Z' Score for bankruptcy prediction (private company formula).
    Z' < 1.23 = distress zone. Z' > 2.90 = safe zone.
    Never use the 1.81 threshold — that is the public company model (uses market cap).
    We use Z' (book equity) because most mid-market suppliers are private.
    See ML_SPEC.md Section 3.1 for full implementation.
    """
    ...

# ❌ Never do this
def calc(wc, ta, re, e, mc, tl, r):
    ...
```

- Use `pydantic` v2 for all data models and validation — never v1 syntax. Key differences:
  - `@field_validator` not `@validator`
  - `model_config = ConfigDict(...)` not `class Config:`
  - `model_dump()` not `.dict()`
  - `model_validate()` not `.parse_obj()`
  - See ADR-011 for complete v1 vs v2 syntax reference
- Use `httpx` for async HTTP calls, never `requests` in async contexts
- All database queries go through repository classes, never raw SQL in endpoints or services (ADR-010). Every repository must have three implementations:
  1. `{Name}Repository` — Protocol (interface defining the contract)
  2. `InMemory{Name}Repository` — for tests (no DB required)
  3. `Postgres{Name}Repository` — production implementation
  Inject via FastAPI `Depends()` — never instantiate repositories directly in routes.
- Log with `structlog`, structured JSON logs only
- Environment variables via `pydantic-settings`, never `os.environ` directly

### TypeScript / React
- Functional components only, no class components
- `React.FC` with explicit prop types
- Custom hooks for all data fetching (`useSupplierRiskScore`, `useAlerts`)
- No inline styles — Tailwind classes only
- Co-locate tests with components (`Component.test.tsx` next to `Component.tsx`)

### Git
- Branch naming: `feat/`, `fix/`, `data/`, `ml/`, `infra/`
- Commit format: `feat(ingestion): add SEC EDGAR 10-K parser`
- Never commit credentials, API keys, or `.env` files
- PR must have passing tests before merge

---

## 6. Domain Vocabulary

Use these terms consistently everywhere — in code, comments, tests, and docs.

| Term | Definition |
|---|---|
| `Supplier` | A company in a customer's supply chain being monitored |
| `SupplierProfile` | All data we hold about a supplier across all signal types |
| `RiskScore` | 0–100 integer. Higher = more risk. Updated every 6h or on trigger |
| `RiskSignal` | A single data point contributing to a score (e.g. one news event) |
| `SignalCategory` | One of: `financial`, `news`, `shipping`, `geopolitical`, `macro` |
| `Alert` | A notification fired when a supplier's score crosses a threshold |
| `Portfolio` | A customer's full list of monitored suppliers |
| `Tenant` | A paying customer organisation (multi-tenant SaaS) |
| `EntityResolution` | Matching a raw company name/string to a canonical supplier ID |
| `HealthScore` | Synonym for RiskScore used in the UI (lower = better for users) |

---

## 7. Data Principles

**Critical rules — never violate these:**

1. **Raw data is immutable.** Once ingested to the data lake, never modify it. All transformations happen downstream.
2. **Every record has a source + timestamp.** No data without provenance.
3. **Supplier entity resolution happens before anything else.** Never write a risk score without first resolving the supplier to a canonical ID.
4. **Treat missing data explicitly.** A missing signal is not the same as a zero signal. Use `None` / `NULL`, not `0`.
5. **Never store PII about individuals.** We monitor companies, not people.
6. **Backfill carefully.** When adding new signals, backfill history before training models on it.

---

## 8. ML Principles

1. **Explainability is non-negotiable.** Every score must have SHAP values. Use `shap.TreeExplainer` (not KernelExplainer — it's slow and approximate). No black-box models in production.
2. **Ship in sequence.** Heuristic scorer (v0) → XGBoost (v1) → revisit neural nets only if XGBoost PR-AUC plateaus below 0.50 at 100K+ labels. Each stage requires the previous to be stable. See ADR-007.
3. **`SupplierFeatureVector` is the contract.** The Pydantic model in `ml/features/feature_vector.py` defines all 46 features, their types, and their `None` semantics. The dbt mart `marts.supplier_feature_vector` must produce column names that match exactly. Change a feature name in dbt → update `SupplierFeatureVector` first. See ML_SPEC.md Section 2.
4. **`None` is not `0`.** A missing signal is not the same as a zero signal. Never substitute `0` for `None` in feature vectors. XGBoost handles `np.nan` natively — pass it through.
5. **Temporal splits only.** Never use random train/test splits on time-series data — it leaks future information. Test set = last 6 months, validation = months 7–9 back from present. See ML_SPEC.md Section 5.
6. **Track every experiment.** Use MLflow. Every training run is logged with params, metrics, and artifacts. Never call `model.fit()` outside `mlflow.start_run()`.
7. **Monitor for drift — two signals.** (a) KS-test on score distributions weekly. (b) Feature importances week-over-week — a feature dropping to near-zero importance usually means a data source is down, not a model problem.
8. **Calibrate probabilities.** Use isotonic regression (> 1000 val samples) or Platt scaling. Raw XGBoost probabilities are not calibrated. A score of 70 should reflect ~70% elevated risk.

---

## 9. What Claude Should Always Do

- **Read the relevant spec before writing any code.** For data work, read `DATA_SOURCES.md`. For ML, read `ML_SPEC.md`. For API, read `API_SPEC.md`.
- **Check `DECISIONS.md` before making architectural choices.** If a decision was already made, follow it. If you disagree, add a new ADR proposing a change.
- **Write tests alongside code.** Not after. Not "TODO: add tests". Now.
- **Update docs when behaviour changes.** If you change an API endpoint, update `API_SPEC.md` in the same commit.
- **Ask before large refactors.** If a task requires touching >5 files or changing a core interface, stop and confirm the approach first.
- **Flag data quality issues immediately.** If you notice missing data, unexpected nulls, or schema mismatches, raise them before proceeding.

---

## 10. What Claude Should Never Do

- **Never hardcode credentials, API keys, or secrets.** Use `.env` via `pydantic-settings`.
- **Never skip entity resolution.** Every supplier reference must go through the resolution layer.
- **Never write a model to production without MLflow tracking.**
- **Never drop or overwrite raw data in the lake.**
- **Never create a new database table without a corresponding dbt model.**
- **Never use `print()` for logging in production code.** Use `structlog`.
- **Never assume a data source is reliable.** Always add null checks and schema validation.
- **Never build what can be bought cheaply.** Auth = Auth0. Email = SendGrid. Payments = Stripe. Focus engineering on the core ML and data IP.

---

## 11. Environment Setup

### Conda Environment (do not create a venv — use the existing conda env)

```bash
# Activate the existing environment — do this before anything else
conda activate genai

# Install any missing project-specific packages into the active env
# pip is smart — it skips packages already installed, only fills gaps
pip install -r requirements.txt -r requirements-dev.txt

# Copy environment variables template (first time only)
cp .env.example .env
# Fill in: POSTGRES_*, KAFKA_*, OPENAI_API_KEY, NEWS_API_KEY, SEC_EDGAR_USER_AGENT
```

> **Important for Claude Code:** Never create a `venv` or suggest `python -m venv`.
> Never run `conda create`. The `genai` conda environment already exists and has
> common ML/data libraries pre-installed (numpy, pandas, torch, sklearn, etc.).
> Only `pip install` packages that are genuinely missing for this project.

### Daily Development

```bash
conda activate genai       # always first

make dev                   # start docker-compose (postgres, redis, kafka, airflow, mailhog)
make test                  # pytest with coverage — must pass before any PR
make lint                  # ruff + mypy — must be clean before any PR

# Manual triggers
make ingest-sec            # trigger SEC ingestion once (for testing)
make score                 # trigger scoring run once (for testing)
make dbt-run               # run dbt models

# Start API (dev)
uvicorn backend.app.main:app --reload

# Teardown
make down                  # docker-compose down -v
```

### Local Services (after `make dev`)

| Service | URL | Credentials |
|---|---|---|
| Airflow UI | http://localhost:8080 | admin / admin |
| MailHog | http://localhost:8025 | none |
| Kafka | localhost:9092 | none |
| Postgres | localhost:5432 | see .env |
| Redis | localhost:6379 | none |

### If a Package Is Missing

```bash
# Check if already installed in genai env
conda activate genai && pip show <package-name>

# Install into genai env (never install globally)
pip install <package-name>

# Then add to requirements.txt with pinned version
pip freeze | grep <package-name> >> requirements.txt
```

---

## 12. Current Open Questions

Track unresolved decisions here. Resolve them by adding an ADR in `DECISIONS.md`.

**Resolved — see DECISIONS.md:**
- ~~Entity resolution strategy~~ → ADR-009: hybrid (rules + GPT-4o-mini for hard cases)
- ~~Self-hosted Kafka vs Confluent Cloud~~ → ADR-005: AWS MSK
- ~~Snowflake vs Postgres~~ → Postgres for prototype. Revisit data warehouse (Snowflake/BigQuery) when hitting scale limits.

**Genuinely open — decision needed before Phase 2:**
- [ ] Score update frequency for premium tiers: every 6h is default — should Pro/Enterprise get real-time (on-signal) scoring? Affects Kafka consumer architecture and cost.
- [ ] EU CSDD compliance module (F09): Phase 3 MVP or Phase 4? Affects Phase 3 scope and whether we need a compliance data model from the start.

---

*Last updated: Project init. Update this file whenever project structure, conventions, or phase changes.*
