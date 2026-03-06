"""Supplier Risk Scoring DAG.

Schedule: Every 6 hours ("0 */6 * * *")
Tasks:
    1. score_suppliers — score all suppliers for the data interval date,
                         persist to scores.supplier_daily_scores,
                         publish scores.updated events to Kafka.

The task calls run_daily_scoring() from ml.scoring.run_scoring, which handles
all error isolation internally — a failure on a single supplier is logged and
counted but never raises. The task only fails if the DB connection or Kafka
setup itself is broken.

Retry: 2 attempts, 10-minute fixed delay.

XCom keys:
    score_suppliers → "summary"   dict   ScoringRunSummary serialised
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from airflow import DAG
from airflow.operators.python import PythonOperator

log = structlog.get_logger()


# ── Default args ───────────────────────────────────────────────────────────────


def _log_failure(context: dict[str, Any]) -> None:
    """Airflow on_failure_callback — logs the error to structlog."""
    dag_obj = context.get("dag")
    task_instance = context.get("task_instance")
    log.error(
        "airflow.task_failed",
        dag_id=dag_obj.dag_id if dag_obj is not None else "unknown",
        task_id=task_instance.task_id if task_instance is not None else "unknown",
        execution_date=str(context.get("execution_date")),
        exception=str(context.get("exception")),
    )


default_args: dict[str, Any] = {
    "owner": "ml-team",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "on_failure_callback": _log_failure,
}


# ── Task functions ─────────────────────────────────────────────────────────────


def score_suppliers(**context: Any) -> None:
    """Score all suppliers for the data interval date and persist results.

    Uses context["ds"] (the data interval start date, YYYY-MM-DD) as the
    feature_date so backfills work correctly. Falls back to today if absent.

    Pushes a serialised ScoringRunSummary dict to XCom under 'summary'.
    """
    from ml.scoring.run_scoring import run_daily_scoring

    ds: str | None = context.get("ds")
    feature_date = date.fromisoformat(ds) if ds else date.today()

    log.info("scoring_dag.task_start", feature_date=str(feature_date))

    summary = asyncio.run(run_daily_scoring(feature_date=feature_date))

    log.info(
        "scoring_dag.task_complete",
        feature_date=str(summary.feature_date),
        total_scored=summary.total_scored,
        high=summary.high_risk_count,
        medium=summary.medium_risk_count,
        low=summary.low_risk_count,
        failures=summary.failure_count,
        duration_seconds=summary.run_duration_seconds,
        model_version=summary.model_version,
    )

    context["ti"].xcom_push(key="summary", value=summary.model_dump(mode="json"))


# ── DAG definition ─────────────────────────────────────────────────────────────


with DAG(
    dag_id="ml_score_suppliers",
    schedule="0 */6 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ml", "scoring", "phase-1"],
    doc_md=__doc__,
) as dag:

    PythonOperator(
        task_id="score_suppliers",
        python_callable=score_suppliers,
    )
