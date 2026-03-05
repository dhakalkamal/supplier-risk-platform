.PHONY: setup dev down test lint ingest-sec dbt-run score help

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
	python -m ml.serving.run_scoring --run-once

dbt-run:
	dbt run --project-dir data/dbt --profiles-dir data/dbt

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
	@echo "  make dbt-run     Run dbt models against Postgres pipeline schema"
