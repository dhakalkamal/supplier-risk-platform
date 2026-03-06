"""News Ingestion DAG.

Schedule: Every 2 hours ("0 */2 * * *")
Tasks:
    1. fetch_articles    — fetch from NewsAPI (GDELT fallback on 429) for all tracked queries
    2. enrich_articles   — run NLP (FinBERT sentiment + topic classification) per article
    3. write_to_staging  — upsert enriched articles to staging.stg_news_sentiment via Postgres
    4. publish_to_kafka  — publish raw articles to raw.news topic for streaming consumers

Error isolation: a failure on a single article in enrich_articles or write_to_staging
is logged and skipped — it does not fail the task or the run.

Retry: 3 attempts, 5-minute delay with exponential backoff (same as ingest_sec_edgar).

XCom keys:
    fetch_articles    → "raw_articles"    list[dict]  serialised RawArticle objects
    enrich_articles   → "enriched"        list[dict]  serialised EnrichedArticle objects
    write_to_staging  → "write_stats"     dict        {written: int, skipped: int}
    publish_to_kafka  → "publish_stats"   dict        {published: int, dlq: int}
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog
from airflow import DAG
from airflow.operators.python import PythonOperator

log = structlog.get_logger()

# Queries used on each run. In Phase 2 these will be pulled from the supplier registry.
_DEFAULT_QUERIES = [
    "supply chain disruption",
    "supplier bankruptcy",
    "factory fire",
    "port strike",
    "semiconductor shortage",
]

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


def fetch_articles(**context: Any) -> None:
    """Fetch articles from NewsAPI for each default query.

    Falls back to GDELTClient automatically when NewsAPI returns 429.
    Deduplication by article_id happens inside each client.

    Pushes a list of serialised RawArticle dicts to XCom under 'raw_articles'.
    An empty list is a valid result — not an error.
    """
    from tenacity import wait_none

    from data.ingestion.news.scraper import GDELTClient, NewsAPIClient, RetryableHTTPError

    log.info("news.fetch_start", queries=_DEFAULT_QUERIES)

    async def _run() -> list[Any]:
        from data.ingestion.news.models import RawArticle

        all_articles: list[RawArticle] = []
        seen_ids: set[str] = set()

        async with NewsAPIClient(retry_wait=wait_none()) as newsapi:
            async with GDELTClient(retry_wait=wait_none()) as gdelt:
                for query in _DEFAULT_QUERIES:
                    try:
                        articles = await newsapi.fetch_recent_articles(
                            query=query, hours_back=2
                        )
                    except RetryableHTTPError:
                        log.warning(
                            "news.newsapi_quota_exhausted",
                            query=query,
                            fallback="gdelt",
                        )
                        articles = await gdelt.fetch_articles(query=query)
                    except Exception as exc:  # noqa: BLE001
                        log.error("news.fetch_error", query=query, error=str(exc))
                        articles = []

                    new = [a for a in articles if a.article_id not in seen_ids]
                    seen_ids.update(a.article_id for a in new)
                    all_articles.extend(new)
                    log.info(
                        "news.query_complete",
                        query=query,
                        new_articles=len(new),
                    )

        return all_articles

    raw_articles = asyncio.run(_run())
    log.info("news.fetch_complete", total=len(raw_articles))
    context["ti"].xcom_push(
        key="raw_articles",
        value=[a.model_dump(mode="json") for a in raw_articles],
    )


def enrich_articles(**context: Any) -> None:
    """Run NLP enrichment on each fetched article.

    Reads 'raw_articles' from XCom. For each article, runs sentiment analysis
    and topic classification via NLPProcessor. A failure on a single article
    is logged and skipped — it does not fail the task.

    Pushes serialised EnrichedArticle dicts to XCom under 'enriched'.
    """
    from data.ingestion.news.models import RawArticle
    from data.ingestion.news.nlp_processor import NLPProcessor

    raw_dicts: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="fetch_articles", key="raw_articles"
    ) or []

    if not raw_dicts:
        log.info("news.enrich_skip", reason="no_articles")
        context["ti"].xcom_push(key="enriched", value=[])
        return

    raw_articles = [RawArticle.model_validate(d) for d in raw_dicts]
    processor = NLPProcessor(use_finbert=True)
    enriched_list = []

    async def _run() -> None:
        for article in raw_articles:
            try:
                enriched = await processor.process_article(article)
                enriched_list.append(enriched)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "news.enrich_article_failed",
                    article_id=article.article_id,
                    error=str(exc),
                )
                # Skip this article — do not fail the whole run

    asyncio.run(_run())
    skipped = len(raw_articles) - len(enriched_list)
    log.info("news.enrich_complete", enriched=len(enriched_list), skipped=skipped)
    context["ti"].xcom_push(
        key="enriched",
        value=[e.model_dump(mode="json") for e in enriched_list],
    )


def write_to_staging(**context: Any) -> None:
    """Upsert enriched articles to staging.stg_news_sentiment in Postgres.

    Reads 'enriched' from XCom. Writes via PostgresNewsRepository (asyncpg pool).
    A failure on a single article is logged and counted as skipped — it does not
    fail the task.

    Pushes write_stats dict to XCom: {written: int, skipped: int}.
    """
    from data.ingestion.news.models import EnrichedArticle

    enriched_dicts: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="enrich_articles", key="enriched"
    ) or []

    if not enriched_dicts:
        log.info("news.write_skip", reason="no_enriched_articles")
        context["ti"].xcom_push(key="write_stats", value={"written": 0, "skipped": 0})
        return

    enriched_articles = [EnrichedArticle.model_validate(d) for d in enriched_dicts]
    stats = {"written": 0, "skipped": 0}

    async def _run() -> None:
        import asyncpg

        from backend.app.config import get_settings
        from data.ingestion.news.consumer import PostgresNewsRepository

        settings = get_settings()
        pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_db,
            min_size=1,
            max_size=3,
        )
        try:
            repo = PostgresNewsRepository(pool=pool)
            for article in enriched_articles:
                try:
                    await repo.upsert_enriched_article(article)
                    stats["written"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "news.write_article_failed",
                        article_id=article.article_id,
                        error=str(exc),
                    )
                    stats["skipped"] += 1
        finally:
            await pool.close()

    asyncio.run(_run())
    log.info("news.write_complete", **stats)
    context["ti"].xcom_push(key="write_stats", value=stats)


def publish_to_kafka(**context: Any) -> None:
    """Publish raw articles to the raw.news Kafka topic.

    Reads 'raw_articles' from XCom (the original unfetched versions, not enriched)
    so streaming consumers can run their own enrichment independently.
    Publishes each as a NewsRawEvent. Tracks published vs DLQ counts.

    Pushes publish_stats dict to XCom: {published: int, dlq: int}.
    """

    from data.ingestion.news.models import NewsRawEvent, RawArticle
    from data.pipeline.kafka_producer import SupplierRiskKafkaProducer

    raw_dicts: list[dict[str, Any]] = context["ti"].xcom_pull(
        task_ids="fetch_articles", key="raw_articles"
    ) or []

    if not raw_dicts:
        log.info("kafka.publish_skip", reason="no_articles")
        context["ti"].xcom_push(key="publish_stats", value={"published": 0, "dlq": 0})
        return

    raw_articles = [RawArticle.model_validate(d) for d in raw_dicts]
    stats = {"published": 0, "dlq": 0}

    async def _run() -> None:
        async with SupplierRiskKafkaProducer() as producer:
            for article in raw_articles:
                event = NewsRawEvent(
                    source=article.ingestion_source,  # type: ignore[arg-type]
                    article_id=article.article_id,
                    url=article.url,
                    title=article.title,
                    content=article.content,
                    published_at=article.published_at,
                    source_name=article.source_name,
                    ingested_at=article.ingested_at,
                )
                try:
                    await producer._publish(
                        topic=producer.TOPIC_MAP["news"],
                        payload=event.model_dump(mode="json"),
                        key=article.article_id,
                    )
                    stats["published"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "news.kafka_publish_failed",
                        article_id=article.article_id,
                        error=str(exc),
                    )
                    stats["dlq"] += 1

    asyncio.run(_run())
    log.info("kafka.publish_complete", **stats)
    context["ti"].xcom_push(key="publish_stats", value=stats)


# ── DAG definition ─────────────────────────────────────────────────────────────


with DAG(
    dag_id="ingest_news",
    schedule="0 */2 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["ingestion", "news", "phase-1"],
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(
        task_id="fetch_articles",
        python_callable=fetch_articles,
    )

    t2 = PythonOperator(
        task_id="enrich_articles",
        python_callable=enrich_articles,
    )

    t3 = PythonOperator(
        task_id="write_to_staging",
        python_callable=write_to_staging,
    )

    t4 = PythonOperator(
        task_id="publish_to_kafka",
        python_callable=publish_to_kafka,
    )

    t1 >> t2 >> t3 >> t4
