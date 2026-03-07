"""News repository — fetch NLP-enriched news articles linked to a supplier.

Protocol + InMemory + Postgres pattern (ADR-010).
Queries staging.stg_news_sentiment (written by Session 2 news pipeline).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import asyncpg
import structlog

from backend.app.models.requests import SupplierNewsParams
from backend.app.models.responses import NewsArticleResponse

log = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@runtime_checkable
class NewsRepository(Protocol):
    async def get_supplier_news(
        self,
        supplier_id: str,
        params: SupplierNewsParams,
    ) -> tuple[list[NewsArticleResponse], int]:
        """Return paginated news articles for a supplier, newest first."""
        ...


class InMemoryNewsRepository:
    """In-memory news repository for unit tests."""

    def __init__(self) -> None:
        self._articles: list[dict[str, Any]] = []

    def seed_article(self, article: dict[str, Any]) -> None:
        self._articles.append(article)

    async def get_supplier_news(
        self,
        supplier_id: str,
        params: SupplierNewsParams,
    ) -> tuple[list[NewsArticleResponse], int]:
        from datetime import timedelta

        cutoff = _now() - timedelta(days=params.days)
        articles = [
            a
            for a in self._articles
            if a.get("supplier_id") == supplier_id
            and a.get("published_at", _now()) >= cutoff
        ]

        if params.sentiment:
            articles = [
                a for a in articles if a.get("sentiment_label") == params.sentiment
            ]

        articles.sort(key=lambda a: a.get("published_at", _now()), reverse=True)
        total = len(articles)
        start = (params.page - 1) * params.per_page
        page = articles[start : start + params.per_page]

        return [
            NewsArticleResponse(
                article_id=a["article_id"],
                title=a["title"],
                url=a.get("url", ""),
                source_name=a.get("source_name", ""),
                source_credibility=a.get("source_credibility"),
                published_at=a["published_at"],
                sentiment_score=a.get("sentiment_score", 0.0),
                sentiment_label=a.get("sentiment_label", "neutral"),
                sentiment_model=a.get("sentiment_model", "finbert"),
                topics=a.get("topics", []),
                score_contribution=a.get("score_contribution"),
                content_available=a.get("content_available", False),
            )
            for a in page
        ], total


class PostgresNewsRepository:
    """Production news repository — queries staging.stg_news_sentiment."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_supplier_news(
        self,
        supplier_id: str,
        params: SupplierNewsParams,
    ) -> tuple[list[NewsArticleResponse], int]:
        conditions = [
            "supplier_id = $1",
            "published_at >= NOW() - ($2 * INTERVAL '1 day')",
        ]
        args: list[Any] = [supplier_id, params.days]
        n = 3

        if params.sentiment:
            conditions.append(f"sentiment_label = ${n}")
            args.append(params.sentiment)
            n += 1

        where = " AND ".join(conditions)
        offset = (params.page - 1) * params.per_page

        query = f"""
            SELECT
                article_id,
                title,
                url,
                source_name,
                NULL::FLOAT        AS source_credibility,
                published_at,
                sentiment_score,
                sentiment_label,
                'finbert'          AS sentiment_model,
                COALESCE(topics, ARRAY[]::TEXT[]) AS topics,
                NULL::INTEGER      AS score_contribution,
                (content IS NOT NULL AND content <> '') AS content_available,
                COUNT(*) OVER()    AS total_count
            FROM staging.stg_news_sentiment
            WHERE {where}
            ORDER BY published_at DESC
            LIMIT ${n} OFFSET ${n + 1}
        """
        args.extend([params.per_page, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)

        if not rows:
            return [], 0

        total = rows[0]["total_count"]
        articles = [
            NewsArticleResponse(
                article_id=row["article_id"],
                title=row["title"],
                url=row["url"] or "",
                source_name=row["source_name"] or "",
                source_credibility=row["source_credibility"],
                published_at=row["published_at"],
                sentiment_score=row["sentiment_score"] or 0.0,
                sentiment_label=row["sentiment_label"] or "neutral",
                sentiment_model=row["sentiment_model"],
                topics=list(row["topics"] or []),
                score_contribution=row["score_contribution"],
                content_available=row["content_available"] or False,
            )
            for row in rows
        ]
        return articles, total
