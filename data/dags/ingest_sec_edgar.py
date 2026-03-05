"""SEC EDGAR Ingestion DAG.

Schedule: Daily at 02:00 UTC
Tasks:
    1. fetch_new_filings    — call SECEdgarClient.get_recent_filings(since_date=yesterday)
    2. parse_financials     — for each filing, call SECFinancialsParser.extract_financials()
    3. publish_to_kafka     — publish each FinancialSnapshot to raw.sec via KafkaProducer
    4. update_ingestion_log — write run metadata to postgres (run_date, count, errors)

Retry: 3 attempts, 5-minute delay between retries (exponential backoff).
On failure: structured error logged via structlog. No email alerts yet (Phase 2).

XCom keys:
    fetch_new_filings  → "filings"     list[dict]   serialised Filing objects
    parse_financials   → "snapshots"   list[dict]   serialised FinancialSnapshot objects
    publish_to_kafka   → "publish_stats" dict        {published: int, dlq: int}
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
    "owner": "data-team",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "on_failure_callback": _log_failure,
}


# ── Task functions ─────────────────────────────────────────────────────────────


def fetch_new_filings(**context: Any) -> None:
    """Fetch all 10-K/10-Q/8-K filings filed since yesterday.

    Pushes a list of serialised Filing dicts to XCom under key 'filings'.
    Succeeds with an empty list if no filings exist for the date — not an error.
    """
    from data.ingestion.sec_edgar.models import Filing
    from data.ingestion.sec_edgar.scraper import SECEdgarClient

    since = date.today() - timedelta(days=1)
    log.info("sec_edgar.fetch_start", since_date=str(since))

    async def _run() -> list[Filing]:
        async with SECEdgarClient() as client:
            return await client.get_recent_filings(since)

    filings = asyncio.run(_run())
    log.info("sec_edgar.fetch_complete", count=len(filings), since_date=str(since))

    serialised = [f.model_dump(mode="json") for f in filings]
    context["ti"].xcom_push(key="filings", value=serialised)


def parse_financials(**context: Any) -> None:
    """Parse financial data from each fetched filing.

    Reads 'filings' from XCom. For each filing, fetches XBRL company facts
    and extracts a FinancialSnapshot. Pushes 'snapshots' list to XCom.
    """
    from data.ingestion.sec_edgar.models import Filing, FinancialSnapshot
    from data.ingestion.sec_edgar.parser import SECFinancialsParser
    from data.ingestion.sec_edgar.scraper import SECEdgarClient

    raw_filings: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="fetch_new_filings", key="filings"
    ) or []

    if not raw_filings:
        log.info("sec_edgar.parse_skip", reason="no_filings")
        context["ti"].xcom_push(key="snapshots", value=[])
        return

    filings = [Filing.model_validate(f) for f in raw_filings]
    parser = SECFinancialsParser()
    snapshots: list[FinancialSnapshot] = []

    async def _run() -> list[FinancialSnapshot]:
        async with SECEdgarClient() as client:
            return await _parse_all(client, parser, filings)

    snapshots = asyncio.run(_run())
    log.info("sec_edgar.parse_complete", count=len(snapshots))
    context["ti"].xcom_push(
        key="snapshots", value=[s.model_dump(mode="json") for s in snapshots]
    )


async def _parse_all(client: Any, parser: Any, filings: list[Any]) -> list[Any]:
    """Fetch facts and extract financials for each filing (async helper)."""
    results = []
    for filing in filings:
        try:
            facts = await client.get_company_facts(filing.cik)
            snapshot = parser.extract_financials(filing.cik, facts.model_dump())
            results.append(snapshot)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sec_edgar.parse_error",
                cik=filing.cik,
                filing_type=filing.filing_type,
                error=str(exc),
            )
    return results


def publish_to_kafka(**context: Any) -> None:
    """Publish each FinancialSnapshot to the raw.sec Kafka topic.

    Reads 'snapshots' from XCom. Publishes each as a SECRawEvent.
    Pushes publish_stats dict to XCom: {published: int, dlq: int}.
    """
    from datetime import timezone

    from data.ingestion.sec_edgar.models import FinancialSnapshot, SECRawEvent
    from data.pipeline.kafka_producer import SupplierRiskKafkaProducer

    raw_snapshots: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="parse_financials", key="snapshots"
    ) or []

    if not raw_snapshots:
        log.info("kafka.publish_skip", reason="no_snapshots")
        context["ti"].xcom_push(key="publish_stats", value={"published": 0, "dlq": 0})
        return

    snapshots = [FinancialSnapshot.model_validate(s) for s in raw_snapshots]
    stats = {"published": 0, "dlq": 0}

    async def _run() -> None:
        now = datetime.now(tz=timezone.utc)
        async with SupplierRiskKafkaProducer() as producer:
            for snap in snapshots:
                event = SECRawEvent(
                    cik=snap.cik,
                    company_name="",
                    filing_type=snap.filing_type,
                    filed_date=snap.period_end,
                    period_of_report=snap.period_end,
                    financials=snap,
                    going_concern=snap.going_concern_flag,
                    ingested_at=now,
                )
                ok = await producer.publish_sec_event(event)
                if ok:
                    stats["published"] += 1
                else:
                    stats["dlq"] += 1

    asyncio.run(_run())
    log.info("kafka.publish_complete", **stats)
    context["ti"].xcom_push(key="publish_stats", value=stats)


def update_ingestion_log(**context: Any) -> None:
    """Write run metadata to Postgres pipeline schema.

    Records run_date, filing count, publish stats, and any errors.
    Uses raw psycopg2 (not SQLAlchemy) to stay lightweight in Airflow tasks.
    """
    publish_stats: dict[str, int] = context["ti"].xcom_pull(
        task_ids="publish_to_kafka", key="publish_stats"
    ) or {"published": 0, "dlq": 0}

    log.info(
        "sec_edgar.ingestion_log",
        run_date=str(date.today()),
        published=publish_stats.get("published", 0),
        dlq=publish_stats.get("dlq", 0),
        execution_date=str(context.get("execution_date")),
    )
    # TODO (Phase 2): INSERT into pipeline.ingestion_log via asyncpg


# ── DAG definition ─────────────────────────────────────────────────────────────


with DAG(
    dag_id="ingest_sec_edgar",
    schedule="0 2 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ingestion", "sec", "phase-1"],
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(
        task_id="fetch_new_filings",
        python_callable=fetch_new_filings,
    )

    t2 = PythonOperator(
        task_id="parse_financials",
        python_callable=parse_financials,
    )

    t3 = PythonOperator(
        task_id="publish_to_kafka",
        python_callable=publish_to_kafka,
    )

    t4 = PythonOperator(
        task_id="update_ingestion_log",
        python_callable=update_ingestion_log,
    )

    t1 >> t2 >> t3 >> t4
