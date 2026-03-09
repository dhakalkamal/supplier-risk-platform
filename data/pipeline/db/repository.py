"""
Postgres repository for the pipeline schema.
Writes validated raw events to pipeline.raw_* tables.

Local dev:  postgres:15 in Docker  (DATABASE_URL from env)
Production: AWS RDS Multi-AZ       (same interface, different creds)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

from pipeline.schemas.raw_events import (
    SECRawEvent,
    NewsRawEvent,
    AISRawEvent,
    MacroRawEvent,
    GeoRawEvent,
)

logger = logging.getLogger(__name__)


class PipelineRepository:
    """All write operations to the pipeline schema."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ─────────────────────────────────────────────────────────────────────────
    # SEC
    # ─────────────────────────────────────────────────────────────────────────

    async def insert_sec_filing(self, event: SECRawEvent) -> None:
        sql = """
            INSERT INTO pipeline.raw_sec_filings
                (cik, company_name, filing_type, filed_date,
                 period_of_report, financials, going_concern, ingested_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT DO NOTHING
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                event.cik,
                event.company_name,
                event.filing_type,
                event.filed_date,
                event.period_of_report,
                event.financials,          # asyncpg serialises dict → JSONB
                event.going_concern,
                event.ingested_at,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # News
    # ─────────────────────────────────────────────────────────────────────────

    async def insert_news_article(self, event: NewsRawEvent) -> None:
        sql = """
            INSERT INTO pipeline.raw_news_articles
                (article_id, url, title, content, published_at,
                 source_name, ingestion_source, ingested_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (article_id) DO NOTHING
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                event.article_id,
                str(event.url),
                event.title,
                event.content,
                event.published_at,
                event.source_name,
                event.ingestion_source,
                event.ingested_at,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # AIS
    # ─────────────────────────────────────────────────────────────────────────

    async def insert_ais_event(self, event: AISRawEvent) -> None:
        sql = """
            INSERT INTO pipeline.raw_ais_events
                (port_id, port_name, vessel_mmsi, vessel_name,
                 arrival_time, departure_time, cargo_type, ingested_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                event.port_id,
                event.port_name,
                event.vessel_mmsi,
                event.vessel_name,
                event.arrival_time,
                event.departure_time,
                event.cargo_type,
                event.ingested_at,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Macro
    # ─────────────────────────────────────────────────────────────────────────

    async def insert_macro_series(self, event: MacroRawEvent) -> None:
        sql = """
            INSERT INTO pipeline.raw_macro_series
                (series_id, series_name, observation_date, value, unit, ingested_at)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (series_id, observation_date) DO UPDATE
                SET value = EXCLUDED.value,
                    ingested_at = EXCLUDED.ingested_at
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                event.series_id,
                event.series_name,
                event.observation_date,
                event.value,
                event.unit,
                event.ingested_at,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Geo
    # ─────────────────────────────────────────────────────────────────────────

    async def insert_geo_event(self, event: GeoRawEvent) -> None:
        sql = """
            INSERT INTO pipeline.raw_geo_events
                (event_id, event_type, country, region,
                 event_date, severity, source, ingested_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (event_id) DO NOTHING
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                event.event_id,
                event.event_type,
                event.country,
                event.region,
                event.event_date,
                event.severity,
                event.source,
                event.ingested_at,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Pool factory
# ─────────────────────────────────────────────────────────────────────────────

async def create_pool(database_url: str) -> asyncpg.Pool:
    """Create a connection pool. Call once at startup."""
    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("Postgres pool created", extra={"dsn": database_url.split("@")[-1]})
    return pool


@asynccontextmanager
async def get_repository(database_url: str) -> AsyncGenerator[PipelineRepository, None]:
    """Context manager for scripts / tests that manage their own pool lifecycle."""
    pool = await create_pool(database_url)
    try:
        yield PipelineRepository(pool)
    finally:
        await pool.close()
