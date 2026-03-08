.PHONY: setup dev down test lint ingest-sec dbt-run score worker consume-scores frontend-dev frontend-build frontend-lint docker-build docker-prod deploy-schema help

# ── Setup ─────────────────────────────────────────────────────────────────────

setup:
	pip install -r requirements.txt -r requirements-dev.txt
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example — fill in your values before running"; fi

# ── Docker services ───────────────────────────────────────────────────────────

dev:
	docker-compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
	@docker-compose ps

down:
	docker-compose down -v

# ── Tests and lint ────────────────────────────────────────────────────────────

test:
	pytest tests/ -v --cov=data --cov=backend --cov-report=term-missing --cov-fail-under=80

lint:
	ruff check .
	mypy data/ backend/ --ignore-missing-imports

# ── Manual pipeline triggers ──────────────────────────────────────────────────

ingest-sec:
	python -m data.ingestion.sec_edgar.scraper --run-once --since-date yesterday

score:
	python -m ml.scoring.run_scoring

dbt-run:
	dbt run --project-dir data/dbt --profiles-dir data/dbt

worker:
	conda run -n genai celery -A backend.app.worker.celery_app worker --loglevel=info -Q notifications,websocket

consume-scores:
	conda run -n genai python -m backend.app.consumers.scores_consumer

# ── Frontend ──────────────────────────────────────────────────────────────────

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-lint:
	cd frontend && npm run lint && npm run typecheck

# ── Docker production ─────────────────────────────────────────────────────────

docker-build:
	docker-compose -f docker-compose.prod.yml build

docker-prod:
	docker-compose -f docker-compose.prod.yml up

deploy-schema:
	railway run python -c "\
import asyncio, asyncpg, pathlib, os; \
async def apply(): \
    conn = await asyncpg.connect(os.environ['DATABASE_URL']); \
    sql = pathlib.Path('backend/app/db/migrations/001_initial_schema.sql').read_text(); \
    await conn.execute(sql); \
    await conn.close(); \
    print('Schema applied on Railway'); \
asyncio.run(apply())"

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo "Available commands:"
	@echo "  make setup       Install dependencies (into active conda env)"
	@echo "  make dev         Start local services via docker-compose"
	@echo "  make down        Stop and remove local services (docker-compose down -v)"
	@echo "  make test        Run pytest with coverage (must be >= 80%)"
	@echo "  make lint        Run ruff + mypy"
	@echo "  make ingest-sec  Trigger SEC EDGAR ingestion manually (one run)"
	@echo "  make score       Trigger scoring run manually (one run)"
	@echo "  make dbt-run          Run dbt models against Postgres pipeline schema"
	@echo "  make frontend-dev     Start Vite dev server (http://localhost:5173)"
	@echo "  make frontend-build   Production build (dist/)"
	@echo "  make frontend-lint    Run ESLint + TypeScript typecheck"
	@echo "  make docker-build     Build prod Docker images (backend + frontend)"
	@echo "  make docker-prod      Run prod Docker stack locally (docker-compose.prod.yml)"
	@echo "  make deploy-schema    Apply DB migration on Railway (requires railway CLI)"
