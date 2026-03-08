# Supplier Risk Intelligence Platform

Real-time supplier risk monitoring for procurement teams.
Scores suppliers 0–100 using public signals (SEC filings, news, shipping, geopolitical data).

## Quick Start (local)

```bash
# 1. Copy env template and fill in your API keys
cp .env.example .env

# 2. Start Docker services (Postgres, Redis, Kafka, Mailpit)
make dev

# 3. Apply the database schema
conda activate genai
python -c "
import asyncio, asyncpg, pathlib
from backend.app.config import get_settings

async def apply():
    s = get_settings()
    conn = await asyncpg.connect(s.database_url.replace('+asyncpg', ''))
    sql = pathlib.Path('backend/app/db/migrations/001_initial_schema.sql').read_text()
    await conn.execute(sql)
    await conn.close()
    print('Schema applied')

asyncio.run(apply())
"

# 4. Start the API
uvicorn backend.app.main:app --reload --port 8000

# 5. Start the UI
cd frontend && npm run dev
# Open http://localhost:5173
```

> **Auth0 dev bypass:** Leave `AUTH0_DOMAIN` empty in `.env` (the default) to skip
> JWT validation locally. All requests are treated as an admin tenant — no Auth0
> account required for local development.

## Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI + Python 3.11 |
| Frontend | React + TypeScript + Tailwind |
| Database | Postgres 15 |
| Cache / broker | Redis 7 |
| Event streaming | Apache Kafka (AWS MSK in prod) |
| Data pipeline | dbt + Apache Airflow |
| ML scoring | Heuristic scorer v0 (XGBoost in Phase 2) |
| Auth | Auth0 (JWT + OAuth2) |
| Email | SendGrid |
| Deploy | Railway |

## Common commands

```bash
make dev            # Start Docker services
make down           # Stop and remove Docker services
make test           # Run pytest with coverage (>= 80% required)
make lint           # ruff + mypy
make score          # Trigger a scoring run manually
make dbt-run        # Run dbt models against Postgres
make docker-build   # Build production Docker images
make docker-prod    # Run production stack locally
make deploy-schema  # Apply DB migration on Railway
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Deployment (Railway)

1. Push to GitHub
2. Railway → New Project → Deploy from GitHub
3. Add Postgres + Redis plugins
4. Set env vars in Railway dashboard (see `.env.example` for the full list)
5. `make deploy-schema` to apply the DB migration
6. Update Auth0 Allowed Callback/Logout/Web Origins with the Railway frontend URL

## Session Prompts

Development is structured in sessions. See [prompts/](prompts/) for each session's scope.
