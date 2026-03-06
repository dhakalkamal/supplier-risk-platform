"""Scoring runner — entry point for the daily supplier risk scoring pipeline.

Called by Airflow DAG ml_score_suppliers (every 6 hours) and by `make score`.

Steps:
    1. Read supplier_feature_vector from marts.supplier_feature_vector (Postgres)
    2. Score each supplier via HeuristicRiskScorer
    3. Write DailyScoreRecord to scores.supplier_daily_scores
    4. Publish scores.updated event to Kafka (consumed by the alert engine)
    5. Log summary: total scored, high/medium/low breakdown, failures

Manual trigger:
    python -m ml.scoring.run_scoring --date 2025-03-04
    python -m ml.scoring.run_scoring          # defaults to today
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
import structlog
from aiokafka import AIOKafkaProducer
from pydantic import BaseModel

from backend.app.config import Settings, get_settings
from ml.features.feature_vector import SupplierFeatureVector
from ml.scoring.heuristic_scorer import HeuristicRiskScorer
from ml.scoring.models import DailyScoreRecord, RiskScoreOutput
from ml.scoring.score_repository import PostgresScoreRepository

log = structlog.get_logger(__name__)

_TOPIC_SCORES_UPDATED = "scores.updated"
_FEATURE_VECTOR_TABLE = "marts.supplier_feature_vector"


class ScoringRunSummary(BaseModel):
    """Summary of a completed scoring run. Logged and returned to Airflow."""

    feature_date: date
    total_scored: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    failure_count: int
    run_duration_seconds: float
    model_version: str


async def run_daily_scoring(
    feature_date: date | None = None,
    settings: Settings | None = None,
) -> ScoringRunSummary:
    """Score all suppliers for the given feature date and persist results.

    Args:
        feature_date: Date of the feature snapshot to score. Defaults to today.
        settings:     Application settings. Defaults to get_settings() singleton.
    """
    settings = settings or get_settings()
    feature_date = feature_date or date.today()

    dsn = (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    pool = await asyncpg.create_pool(dsn)
    try:
        async with AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            security_protocol=settings.kafka_security_protocol,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        ) as kafka:
            repo = PostgresScoreRepository(pool=pool)
            scorer = HeuristicRiskScorer()
            feature_vectors = await _fetch_feature_vectors(pool, feature_date)
            log.info("scoring.started", feature_date=str(feature_date),
                     supplier_count=len(feature_vectors))
            summary = await _score_all(scorer, repo, kafka, feature_vectors, feature_date)
    finally:
        await pool.close()

    log.info("scoring.completed", **summary.model_dump(mode="json"))
    return summary


async def _fetch_feature_vectors(
    pool: Any, feature_date: date
) -> list[SupplierFeatureVector]:
    """Fetch all supplier feature vectors for a given date from Postgres."""
    query = f"SELECT * FROM {_FEATURE_VECTOR_TABLE} WHERE feature_date = $1"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, feature_date)

    vectors: list[SupplierFeatureVector] = []
    for row in rows:
        try:
            vectors.append(SupplierFeatureVector.model_validate(dict(row)))
        except Exception as exc:  # noqa: BLE001
            log.warning("scoring.invalid_feature_vector",
                        error=str(exc), feature_date=str(feature_date))
    return vectors


async def _score_all(
    scorer: HeuristicRiskScorer,
    repo: PostgresScoreRepository,
    kafka: AIOKafkaProducer,
    feature_vectors: list[SupplierFeatureVector],
    feature_date: date,
) -> ScoringRunSummary:
    """Score all suppliers, persist results, publish events. Never raises."""
    start = datetime.now(tz=timezone.utc)
    counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    failures = 0

    for fv in feature_vectors:
        try:
            output = scorer.score(fv)
            record = DailyScoreRecord.from_score_output(output)
            await repo.upsert_daily_score(record)
            await _publish_score_event(kafka, output)
            counts[output.risk_level] += 1
        except Exception as exc:  # noqa: BLE001
            log.error("scoring.supplier_failed",
                      supplier_id=fv.supplier_id, error=str(exc))
            failures += 1

    duration = (datetime.now(tz=timezone.utc) - start).total_seconds()
    return ScoringRunSummary(
        feature_date=feature_date,
        total_scored=len(feature_vectors) - failures,
        high_risk_count=counts["high"],
        medium_risk_count=counts["medium"],
        low_risk_count=counts["low"],
        failure_count=failures,
        run_duration_seconds=round(duration, 3),
        model_version=HeuristicRiskScorer.MODEL_VERSION,
    )


async def _publish_score_event(
    kafka: AIOKafkaProducer, output: RiskScoreOutput
) -> None:
    """Publish a scores.updated event to Kafka. Logs on failure, never raises."""
    payload = {
        "supplier_id":   output.supplier_id,
        "score":         output.score,
        "risk_level":    output.risk_level,
        "model_version": output.model_version,
        "feature_date":  output.feature_date.isoformat(),
        "scored_at":     output.scored_at.isoformat(),
    }
    try:
        await kafka.send_and_wait(
            _TOPIC_SCORES_UPDATED, value=payload, key=output.supplier_id
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("scoring.kafka_publish_failed",
                    supplier_id=output.supplier_id, error=str(exc))


def main() -> None:
    """CLI entry point: python -m ml.scoring.run_scoring [--date YYYY-MM-DD]."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run supplier risk scoring for a given feature date."
    )
    parser.add_argument(
        "--date", type=str, default=None,
        metavar="YYYY-MM-DD",
        help="Feature date to score. Defaults to today.",
    )
    args = parser.parse_args()
    feature_date = date.fromisoformat(args.date) if args.date else None

    summary = asyncio.run(run_daily_scoring(feature_date=feature_date))
    log.info("scoring.summary", **summary.model_dump(mode="json"))


if __name__ == "__main__":
    main()
