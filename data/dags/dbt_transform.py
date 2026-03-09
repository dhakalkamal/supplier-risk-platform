"""dbt Transform DAG.

Schedule: Triggered by upstream ingest DAGs (not time-based).
Tasks:
    1. run_dbt_raw      — dbt run --select raw.*
    2. run_dbt_staging  — dbt run --select staging.*
    3. run_dbt_marts    — dbt run --select marts.*
    4. run_dbt_tests    — dbt test (data quality checks)
    5. log_transform    — write transform metadata to ingestion_log

This DAG is the bridge between your Kafka consumers and your friend's ML model.
After it completes, supplier_feature_vector is up to date and ml_score_suppliers
can read fresh features.

Flow:
    ingest_* DAGs → [trigger] → dbt_transform → ml_score_suppliers reads features

Failure handling:
  - If raw or staging fails → mark DAG failed, do NOT run marts (bad data)
  - If a single dbt model fails → log error, continue other models (partial ok)
  - dbt test failures → logged as warnings, do NOT block ml_score_suppliers

Retry: 3 attempts, 5-minute exponential backoff.
On failure: structured error logged via structlog. No email alerts (Phase 2).

XCom keys:
    run_dbt_raw     → "raw_result"      dict   {models_run: int, errors: int}
    run_dbt_staging → "staging_result"  dict   {models_run: int, errors: int}
    run_dbt_marts   → "marts_result"    dict   {models_run: int, errors: int}
    run_dbt_tests   → "test_result"     dict   {passed: int, failed: int, warned: int}
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from airflow import DAG
from airflow.operators.python import PythonOperator

log = structlog.get_logger()

# Path to your dbt project inside the repo
_DBT_PROJECT_DIR = "/opt/airflow/data/dbt"


# ── Default args ──────────────────────────────────────────────────────────────


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
    "owner": "data-team",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "on_failure_callback": _log_failure,
}


# ── Shared dbt runner ─────────────────────────────────────────────────────────


def _run_dbt(command: list[str]) -> dict[str, int]:
    """Run a dbt CLI command and return a result summary.

    Returns {models_run: int, errors: int} for dbt run commands,
    or {passed: int, failed: int, warned: int} for dbt test.

    Raises subprocess.CalledProcessError on non-zero exit — triggers Airflow retry.
    """
    full_command = ["dbt"] + command + ["--project-dir", _DBT_PROJECT_DIR]
    log.info("dbt.command_start", command=" ".join(full_command))

    result = subprocess.run(
        full_command,
        capture_output=True,
        text=True,
    )

    # Always log output for debugging in Airflow task logs
    if result.stdout:
        log.info("dbt.stdout", output=result.stdout[-2000:])  # last 2000 chars
    if result.stderr:
        log.warning("dbt.stderr", output=result.stderr[-2000:])

    if result.returncode != 0:
        log.error(
            "dbt.command_failed",
            command=" ".join(full_command),
            returncode=result.returncode,
        )
        raise subprocess.CalledProcessError(result.returncode, full_command)

    log.info("dbt.command_success", command=" ".join(command))

    # Parse summary counts from dbt output
    return _parse_dbt_output(result.stdout, command[0])


def _parse_dbt_output(stdout: str, dbt_command: str) -> dict[str, int]:
    """Extract pass/fail counts from dbt CLI output."""
    import re

    if dbt_command == "test":
        passed = len(re.findall(r"PASS", stdout))
        failed = len(re.findall(r"FAIL", stdout))
        warned = len(re.findall(r"WARN", stdout))
        return {"passed": passed, "failed": failed, "warned": warned}
    else:
        # dbt run output: "Completed with X errors and X warnings"
        errors = len(re.findall(r"ERROR", stdout))
        runs = len(re.findall(r"OK created", stdout))
        return {"models_run": runs, "errors": errors}


# ── Task functions ────────────────────────────────────────────────────────────


def run_dbt_raw(**context: Any) -> None:
    """Run dbt models in the raw/ layer.

    These are views pointing at pipeline.raw_* tables written by Kafka consumers.
    Fast — they are just views, no data movement.
    """
    log.info("dbt.raw_start")
    result = _run_dbt(["run", "--select", "raw.*"])
    log.info("dbt.raw_complete", **result)
    context["ti"].xcom_push(key="raw_result", value=result)


def run_dbt_staging(**context: Any) -> None:
    """Run dbt models in the staging/ layer.

    Cleans, type-casts, and deduplicates raw data.
    One row per natural key (e.g. one row per cik + period_end for SEC).
    """
    log.info("dbt.staging_start")
    result = _run_dbt(["run", "--select", "staging.*"])
    log.info("dbt.staging_complete", **result)
    context["ti"].xcom_push(key="staging_result", value=result)


def run_dbt_marts(**context: Any) -> None:
    """Run dbt models in the marts/ layer.

    Joins staging data to supplier IDs and computes features.
    Final output is supplier_feature_vector — read by ml_score_suppliers.
    """
    log.info("dbt.marts_start")
    result = _run_dbt(["run", "--select", "marts.*"])
    log.info("dbt.marts_complete", **result)
    context["ti"].xcom_push(key="marts_result", value=result)


def run_dbt_tests(**context: Any) -> None:
    """Run dbt data quality tests.

    Tests defined in schema.yml (not_null, unique, accepted_values)
    and custom tests in tests/ (altman_z_score range, feature vector orphans).

    Test failures are logged as warnings — they do NOT block ml_score_suppliers.
    Partial data is better than no scoring run.
    """
    log.info("dbt.tests_start")
    try:
        result = _run_dbt(["test"])
    except subprocess.CalledProcessError:
        # Test failures should not fail the DAG — log and continue
        log.warning("dbt.tests_failed", note="continuing despite test failures")
        result = {"passed": 0, "failed": -1, "warned": 0}

    log.info("dbt.tests_complete", **result)
    context["ti"].xcom_push(key="test_result", value=result)


def log_transform(**context: Any) -> None:
    """Log the full transform run summary."""
    raw_result     = context["ti"].xcom_pull(task_ids="run_dbt_raw",     key="raw_result")     or {}
    staging_result = context["ti"].xcom_pull(task_ids="run_dbt_staging", key="staging_result") or {}
    marts_result   = context["ti"].xcom_pull(task_ids="run_dbt_marts",   key="marts_result")   or {}
    test_result    = context["ti"].xcom_pull(task_ids="run_dbt_tests",   key="test_result")    or {}

    log.info(
        "dbt.transform_summary",
        run_date=str(date.today()),
        dag_run_id=context.get("run_id"),
        raw=raw_result,
        staging=staging_result,
        marts=marts_result,
        tests=test_result,
    )
    # TODO (Phase 2): INSERT into pipeline.ingestion_log via asyncpg


# ── DAG definition ────────────────────────────────────────────────────────────


with DAG(
    dag_id="dbt_transform",
    schedule=None,             # Triggered by ingest_* DAGs, not time-based
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["transform", "dbt", "phase-1"],
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(
        task_id="run_dbt_raw",
        python_callable=run_dbt_raw,
    )

    t2 = PythonOperator(
        task_id="run_dbt_staging",
        python_callable=run_dbt_staging,
    )

    t3 = PythonOperator(
        task_id="run_dbt_marts",
        python_callable=run_dbt_marts,
    )

    t4 = PythonOperator(
        task_id="run_dbt_tests",
        python_callable=run_dbt_tests,
    )

    t5 = PythonOperator(
        task_id="log_transform",
        python_callable=log_transform,
    )

    # raw → staging → marts → tests → log
    t1 >> t2 >> t3 >> t4 >> t5
