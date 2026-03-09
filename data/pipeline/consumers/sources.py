"""
Source-specific Kafka consumers.

One class per topic. Each only needs to declare its topic/source/group_id
and implement _persist() — all retry, DLQ, and logging logic lives in BaseConsumer.
"""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.consumers.base import BaseConsumer
from pipeline.db.repository import PipelineRepository
from pipeline.schemas.raw_events import (
    SECRawEvent,
    NewsRawEvent,
    AISRawEvent,
    MacroRawEvent,
    GeoRawEvent,
)


class SECConsumer(BaseConsumer):
    topic    = "raw.sec"
    source   = "sec"
    group_id = "pipeline-etl-sec"

    async def _persist(self, event: BaseModel, repo: PipelineRepository) -> None:
        assert isinstance(event, SECRawEvent)
        await repo.insert_sec_filing(event)


class NewsConsumer(BaseConsumer):
    topic    = "raw.news"
    source   = "news"
    group_id = "pipeline-nlp-news"

    async def _persist(self, event: BaseModel, repo: PipelineRepository) -> None:
        assert isinstance(event, NewsRawEvent)
        await repo.insert_news_article(event)


class AISConsumer(BaseConsumer):
    topic    = "raw.ais"
    source   = "ais"
    group_id = "pipeline-etl-ais"

    async def _persist(self, event: BaseModel, repo: PipelineRepository) -> None:
        assert isinstance(event, AISRawEvent)
        await repo.insert_ais_event(event)


class MacroConsumer(BaseConsumer):
    topic    = "raw.macro"
    source   = "macro"
    group_id = "pipeline-etl-macro"

    async def _persist(self, event: BaseModel, repo: PipelineRepository) -> None:
        assert isinstance(event, MacroRawEvent)
        await repo.insert_macro_series(event)


class GeoConsumer(BaseConsumer):
    topic    = "raw.geo"
    source   = "geo"
    group_id = "pipeline-etl-geo"

    async def _persist(self, event: BaseModel, repo: PipelineRepository) -> None:
        assert isinstance(event, GeoRawEvent)
        await repo.insert_geo_event(event)


ALL_CONSUMERS: list[type[BaseConsumer]] = [
    SECConsumer,
    NewsConsumer,
    AISConsumer,
    MacroConsumer,
    GeoConsumer,
]
