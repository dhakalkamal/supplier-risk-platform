"""AIS Vessel Tracking Ingestion DAG.

Schedule: Every 4 hours ("0 */4 * * *")
Tasks:
    1. fetch_ais_events     — call MarineTrafficClient.get_port_events(since=4h ago)
    2. publish_to_kafka     — publish each AISRawEvent to raw.ais Kafka topic
    3. update_ingestion_log — write run metadata to postgres ingestion_log

A failure on a single vessel event is logged and counted as DLQ —
it never fails the whole task. Partial data is better than no data.

Retry: 3 attempts, 5-minute exponential backoff.
On failure: structured error logged via structlog. No email alerts (Phase 2).

XCom keys:
    fetch_ais_events   → "ais_events"     list[dict]  serialised AISRawEvent objects
    publish_to_kafka   → "publish_stats"  dict        {published: int, dlq: int}
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from airflow import DAG
from airflow.operators.python import PythonOperator

log = structlog.get_logger()


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


def fetch_ais_events(**context: Any) -> None:
    """Fetch vessel port arrival/departure events from MarineTraffic.

    Looks back 4 hours (matching the DAG schedule) so no events are missed
    between runs. Deduplication by event_id happens inside the client.

    Pushes a list of serialised AISRawEvent dicts to XCom under 'ais_events'.
    An empty list is valid — not an error (quiet shipping period).
    """
    from data.ingestion.ais.scraper import MarineTrafficClient

    since = datetime.now(tz=timezone.utc) - timedelta(hours=4)
    log.info("ais.fetch_start", since=str(since))

    async def _run() -> list[Any]:
        async with MarineTrafficClient() as client:
            return await client.get_port_events(since=since)

    events = asyncio.run(_run())
    log.info("ais.fetch_complete", count=len(events))

    context["ti"].xcom_push(
        key="ais_events",
        value=[e.model_dump(mode="json") for e in events],
    )


def publish_to_kafka(**context: Any) -> None:
    """Publish each AIS event to the raw.ais Kafka topic.

    Reads 'ais_events' from XCom. A failure on a single event is logged
    and counted as DLQ — it never fails the whole task.

    Pushes publish_stats dict to XCom: {published: int, dlq: int}.
    """
    from data.ingestion.ais.models import AISRawEvent
    from data.pipeline.kafka_producer import SupplierRiskKafkaProducer

    raw_events: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="fetch_ais_events", key="ais_events"
    ) or []

    if not raw_events:
        log.info("kafka.publish_skip", reason="no_ais_events")
        context["ti"].xcom_push(key="publish_stats", value={"published": 0, "dlq": 0})
        return

    events = [AISRawEvent.model_validate(e) for e in raw_events]
    stats = {"published": 0, "dlq": 0}

    async def _run() -> None:
        async with SupplierRiskKafkaProducer() as producer:
            for event in events:
                try:
                    ok = await producer.publish_ais_event(event)
                    if ok:
                        stats["published"] += 1
                    else:
                        stats["dlq"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "ais.kafka_publish_failed",
                        vessel_mmsi=event.vessel_mmsi,
                        error=str(exc),
                    )
                    stats["dlq"] += 1

    asyncio.run(_run())
    log.info("kafka.publish_complete", **stats)
    context["ti"].xcom_push(key="publish_stats", value=stats)


def update_ingestion_log(**context: Any) -> None:
    """Write run metadata to Postgres ingestion_log table.

    Records source, run_date, record counts, and status.
    Writes 'partial' if any events went to DLQ, 'success' if all published.
    """
    from datetime import date

    publish_stats: dict[str, int] = context["ti"].xcom_pull(
        task_ids="publish_to_kafka", key="publish_stats"
    ) or {"published": 0, "dlq": 0}

    published = publish_stats.get("published", 0)
    dlq = publish_stats.get("dlq", 0)
    status = "partial" if dlq > 0 else "success"

    log.info(
        "ais.ingestion_log",
        source="ais",
        run_date=str(date.today()),
        records_written=published,
        records_failed=dlq,
        status=status,
        dag_run_id=context.get("run_id"),
    )
    # TODO (Phase 2): INSERT into pipeline.ingestion_log via asyncpg


# ── DAG definition ────────────────────────────────────────────────────────────


with DAG(
    dag_id="ingest_ais",
    schedule="0 */4 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ingestion", "ais", "phase-1"],
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(
        task_id="fetch_ais_events",
        python_callable=fetch_ais_events,
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
