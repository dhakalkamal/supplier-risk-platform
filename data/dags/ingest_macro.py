"""Macroeconomic Series Ingestion DAG.

Schedule: Daily at 06:00 UTC ("0 6 * * *")
Tasks:
    1. fetch_macro_series   — call FREDClient.get_latest_observations() for all tracked series
    2. publish_to_kafka     — publish each MacroRawEvent to raw.macro Kafka topic
    3. update_ingestion_log — write run metadata to postgres ingestion_log

FRED data is daily/weekly/monthly — running once per day is sufficient.
A failure on a single series is logged and skipped — never fails the whole task.

Retry: 3 attempts, 5-minute exponential backoff.
On failure: structured error logged via structlog. No email alerts (Phase 2).

XCom keys:
    fetch_macro_series → "macro_events"   list[dict]  serialised MacroRawEvent objects
    publish_to_kafka   → "publish_stats"  dict        {published: int, dlq: int}
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from airflow import DAG
from airflow.operators.python import PythonOperator

log = structlog.get_logger()

# FRED series tracked — matches ML_SPEC.md macro feature list
_TRACKED_SERIES = [
    "T10Y2Y",       # yield curve spread (recession indicator)
    "UNRATE",       # US unemployment rate
    "CPIAUCSL",     # CPI inflation
    "DCOILWTICO",   # WTI crude oil price
    "PPIACO",       # producer price index
    "UMCSENT",      # consumer sentiment
    "ISM_MAN_PMI",  # manufacturing PMI
    "FEDFUNDS",     # federal funds rate
]


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


# ── Task functions ────────────────────────────────────────────────────────────


def fetch_macro_series(**context: Any) -> None:
    """Fetch latest observations for all tracked FRED series.

    Uses context["ds"] as the observation date so backfills work correctly.
    A failure on a single series is logged and skipped — not an error.

    Pushes a list of serialised MacroRawEvent dicts to XCom under 'macro_events'.
    """
    from data.ingestion.macro.scraper import FREDClient

    ds: str | None = context.get("ds")
    observation_date = date.fromisoformat(ds) if ds else date.today()

    log.info(
        "macro.fetch_start",
        observation_date=str(observation_date),
        series_count=len(_TRACKED_SERIES),
    )

    async def _run() -> list[Any]:
        all_events = []
        async with FREDClient() as client:
            for series_id in _TRACKED_SERIES:
                try:
                    event = await client.get_latest_observation(
                        series_id=series_id,
                        observation_date=observation_date,
                    )
                    if event is not None:
                        all_events.append(event)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "macro.fetch_series_failed",
                        series_id=series_id,
                        error=str(exc),
                    )
                    # Skip this series — partial data is better than no data
        return all_events

    events = asyncio.run(_run())
    log.info("macro.fetch_complete", count=len(events))

    context["ti"].xcom_push(
        key="macro_events",
        value=[e.model_dump(mode="json") for e in events],
    )


def publish_to_kafka(**context: Any) -> None:
    """Publish each MacroRawEvent to the raw.macro Kafka topic.

    Reads 'macro_events' from XCom. A failure on a single event is logged
    and counted as DLQ — it never fails the whole task.

    Pushes publish_stats dict to XCom: {published: int, dlq: int}.
    """
    from data.ingestion.macro.models import MacroRawEvent
    from data.pipeline.kafka_producer import SupplierRiskKafkaProducer

    raw_events: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="fetch_macro_series", key="macro_events"
    ) or []

    if not raw_events:
        log.info("kafka.publish_skip", reason="no_macro_events")
        context["ti"].xcom_push(key="publish_stats", value={"published": 0, "dlq": 0})
        return

    events = [MacroRawEvent.model_validate(e) for e in raw_events]
    stats = {"published": 0, "dlq": 0}

    async def _run() -> None:
        async with SupplierRiskKafkaProducer() as producer:
            for event in events:
                try:
                    ok = await producer.publish_macro_event(event)
                    if ok:
                        stats["published"] += 1
                    else:
                        stats["dlq"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "macro.kafka_publish_failed",
                        series_id=event.series_id,
                        error=str(exc),
                    )
                    stats["dlq"] += 1

    asyncio.run(_run())
    log.info("kafka.publish_complete", **stats)
    context["ti"].xcom_push(key="publish_stats", value=stats)


def update_ingestion_log(**context: Any) -> None:
    """Write run metadata to Postgres ingestion_log table."""
    publish_stats: dict[str, int] = context["ti"].xcom_pull(
        task_ids="publish_to_kafka", key="publish_stats"
    ) or {"published": 0, "dlq": 0}

    published = publish_stats.get("published", 0)
    dlq = publish_stats.get("dlq", 0)
    status = "partial" if dlq > 0 else "success"

    log.info(
        "macro.ingestion_log",
        source="macro",
        run_date=str(date.today()),
        records_written=published,
        records_failed=dlq,
        status=status,
        dag_run_id=context.get("run_id"),
    )
    # TODO (Phase 2): INSERT into pipeline.ingestion_log via asyncpg


# ── DAG definition ────────────────────────────────────────────────────────────


with DAG(
    dag_id="ingest_macro",
    schedule="0 6 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ingestion", "macro", "phase-1"],
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(
        task_id="fetch_macro_series",
        python_callable=fetch_macro_series,
    )

    t2 = PythonOperator(
        task_id="publish_to_kafka",
        python_callable=publish_to_kafka,
    )

    t3 = PythonOperator(
        task_id="update_ingestion_log",
        python_callable=update_ingestion_log,
    )

    t1 >> t2 >> t3
