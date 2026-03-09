"""Geopolitical Risk Events Ingestion DAG.

Schedule: Every 6 hours ("0 */6 * * *")
Tasks:
    1. fetch_geo_events     — fetch from ACLED (conflict), NOAA (weather), OFAC (sanctions)
    2. publish_to_kafka     — publish each GeoRawEvent to raw.geo Kafka topic
    3. update_ingestion_log — write run metadata to postgres ingestion_log

Sources run in parallel where possible. A failure on one source
(e.g. ACLED down) does not block the others.

Retry: 3 attempts, 5-minute exponential backoff.
On failure: structured error logged via structlog. No email alerts (Phase 2).

XCom keys:
    fetch_geo_events   → "geo_events"     list[dict]  serialised GeoRawEvent objects
    publish_to_kafka   → "publish_stats"  dict        {published: int, dlq: int}
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
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


def fetch_geo_events(**context: Any) -> None:
    """Fetch geopolitical events from ACLED, NOAA, and OFAC in parallel.

    Each source is fetched independently — a failure on one source is logged
    and skipped, the others continue. Looks back 6 hours to match DAG schedule.

    Pushes a list of serialised GeoRawEvent dicts to XCom under 'geo_events'.
    """
    from data.ingestion.geo.scraper import ACLEDClient, NOAAClient, OFACClient

    since = datetime.now(tz=timezone.utc) - timedelta(hours=6)
    log.info("geo.fetch_start", since=str(since))

    async def _fetch_acled(client: ACLEDClient) -> list[Any]:
        try:
            events = await client.get_conflict_events(since=since)
            log.info("geo.acled_complete", count=len(events))
            return events
        except Exception as exc:  # noqa: BLE001
            log.warning("geo.acled_failed", error=str(exc))
            return []

    async def _fetch_noaa(client: NOAAClient) -> list[Any]:
        try:
            events = await client.get_weather_events(since=since)
            log.info("geo.noaa_complete", count=len(events))
            return events
        except Exception as exc:  # noqa: BLE001
            log.warning("geo.noaa_failed", error=str(exc))
            return []

    async def _fetch_ofac(client: OFACClient) -> list[Any]:
        try:
            # OFAC sanctions list — full refresh each run (list changes infrequently)
            events = await client.get_sanctions_events()
            log.info("geo.ofac_complete", count=len(events))
            return events
        except Exception as exc:  # noqa: BLE001
            log.warning("geo.ofac_failed", error=str(exc))
            return []

    async def _run() -> list[Any]:
        async with ACLEDClient() as acled, NOAAClient() as noaa, OFACClient() as ofac:
            # Run all 3 sources in parallel
            results = await asyncio.gather(
                _fetch_acled(acled),
                _fetch_noaa(noaa),
                _fetch_ofac(ofac),
            )
        # Flatten results from all 3 sources
        return [event for source_events in results for event in source_events]

    events = asyncio.run(_run())
    log.info("geo.fetch_complete", total=len(events))

    context["ti"].xcom_push(
        key="geo_events",
        value=[e.model_dump(mode="json") for e in events],
    )


def publish_to_kafka(**context: Any) -> None:
    """Publish each GeoRawEvent to the raw.geo Kafka topic.

    Reads 'geo_events' from XCom. A failure on a single event is logged
    and counted as DLQ — it never fails the whole task.

    Pushes publish_stats dict to XCom: {published: int, dlq: int}.
    """
    from data.ingestion.geo.models import GeoRawEvent
    from data.pipeline.kafka_producer import SupplierRiskKafkaProducer

    raw_events: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="fetch_geo_events", key="geo_events"
    ) or []

    if not raw_events:
        log.info("kafka.publish_skip", reason="no_geo_events")
        context["ti"].xcom_push(key="publish_stats", value={"published": 0, "dlq": 0})
        return

    events = [GeoRawEvent.model_validate(e) for e in raw_events]
    stats = {"published": 0, "dlq": 0}

    async def _run() -> None:
        async with SupplierRiskKafkaProducer() as producer:
            for event in events:
                try:
                    ok = await producer.publish_geo_event(event)
                    if ok:
                        stats["published"] += 1
                    else:
                        stats["dlq"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "geo.kafka_publish_failed",
                        event_id=event.event_id,
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
        "geo.ingestion_log",
        source="geo",
        run_date=str(date.today()),
        records_written=published,
        records_failed=dlq,
        status=status,
        dag_run_id=context.get("run_id"),
    )
    # TODO (Phase 2): INSERT into pipeline.ingestion_log via asyncpg


# ── DAG definition ────────────────────────────────────────────────────────────


with DAG(
    dag_id="ingest_geo",
    schedule="0 */6 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ingestion", "geo", "phase-1"],
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(
        task_id="fetch_geo_events",
        python_callable=fetch_geo_events,
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
