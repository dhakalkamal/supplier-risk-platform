"""Score repository — latest scores, history, and portfolio-level aggregates.

Protocol + InMemory + Postgres pattern (ADR-010).
Scores are NOT tenant-scoped: one score per supplier per day, shared across tenants.
Inject PostgresScoreRepository via FastAPI Depends() — never instantiate directly.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

import asyncpg
import structlog
from pydantic import BaseModel

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class SupplierScore(BaseModel):
    """A single scored day for a supplier."""

    supplier_id: str
    score: int
    risk_level: str  # 'low' | 'medium' | 'high'
    score_date: date
    signal_breakdown: dict[str, Any]
    model_version: str
    data_completeness: float | None
    scored_at: datetime


class PortfolioSummaryData(BaseModel):
    """Raw aggregate data for the portfolio summary dashboard widget."""

    total_suppliers: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    unread_alerts_count: int
    average_portfolio_score: int
    score_trend_7d: str  # 'improving' | 'worsening' | 'stable'
    last_scored_at: datetime | None
    plan_supplier_limit: int | None
    plan_supplier_used: int


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ScoreRepository(Protocol):
    """Contract for supplier score data access."""

    async def get_latest_score(self, supplier_id: str) -> SupplierScore | None:
        """Return the most recent score for a supplier, or None if never scored."""
        ...

    async def get_score_history(
        self,
        supplier_id: str,
        days: int,
    ) -> list[SupplierScore]:
        """Return daily scores for the last `days` calendar days, newest last."""
        ...

    async def get_portfolio_summary(
        self,
        tenant_id: str,
        plan_limit: int | None,
    ) -> PortfolioSummaryData:
        """Aggregate score statistics for all suppliers in the tenant's portfolio."""
        ...


# ---------------------------------------------------------------------------
# InMemory implementation — for unit tests
# ---------------------------------------------------------------------------


class InMemoryScoreRepository:
    """In-memory score repository. Pre-populate with seed_score()."""

    def __init__(self) -> None:
        # (supplier_id, score_date) → SupplierScore
        self._scores: dict[tuple[str, date], SupplierScore] = {}

    def seed_score(self, score: SupplierScore) -> None:
        self._scores[(score.supplier_id, score.score_date)] = score

    async def get_latest_score(self, supplier_id: str) -> SupplierScore | None:
        candidates = [
            s for (sid, _), s in self._scores.items() if sid == supplier_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.score_date)

    async def get_score_history(
        self,
        supplier_id: str,
        days: int,
    ) -> list[SupplierScore]:
        cutoff = date.today() - timedelta(days=days)
        results = [
            s
            for (sid, d), s in self._scores.items()
            if sid == supplier_id and d >= cutoff
        ]
        return sorted(results, key=lambda s: s.score_date)

    async def get_portfolio_summary(
        self,
        tenant_id: str,
        plan_limit: int | None,
    ) -> PortfolioSummaryData:
        # InMemory impl returns a stub — sufficient for tests that seed specific data
        return PortfolioSummaryData(
            total_suppliers=0,
            high_risk_count=0,
            medium_risk_count=0,
            low_risk_count=0,
            unread_alerts_count=0,
            average_portfolio_score=0,
            score_trend_7d="stable",
            last_scored_at=None,
            plan_supplier_limit=plan_limit,
            plan_supplier_used=0,
        )


# ---------------------------------------------------------------------------
# Postgres implementation — production
# ---------------------------------------------------------------------------


class PostgresScoreRepository:
    """Production score repository backed by asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_latest_score(self, supplier_id: str) -> SupplierScore | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT supplier_id, score, risk_level, score_date,
                       signal_breakdown, model_version, data_completeness, scored_at
                FROM supplier_scores
                WHERE supplier_id = $1
                ORDER BY score_date DESC
                LIMIT 1
                """,
                supplier_id,
            )

        if row is None:
            return None

        return _row_to_score(row)

    async def get_score_history(
        self,
        supplier_id: str,
        days: int,
    ) -> list[SupplierScore]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT supplier_id, score, risk_level, score_date,
                       signal_breakdown, model_version, data_completeness, scored_at
                FROM supplier_scores
                WHERE supplier_id = $1
                  AND score_date >= CURRENT_DATE - ($2 * INTERVAL '1 day')
                ORDER BY score_date ASC
                """,
                supplier_id,
                days,
            )

        return [_row_to_score(row) for row in rows]

    async def get_portfolio_summary(
        self,
        tenant_id: str,
        plan_limit: int | None,
    ) -> PortfolioSummaryData:
        async with self._pool.acquire() as conn:
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(ps.id)                                             AS total_suppliers,
                    COUNT(ps.id) FILTER (WHERE ss.risk_level = 'high')      AS high_risk_count,
                    COUNT(ps.id) FILTER (WHERE ss.risk_level = 'medium')    AS medium_risk_count,
                    COUNT(ps.id) FILTER (WHERE ss.risk_level = 'low')       AS low_risk_count,
                    COALESCE(AVG(ss.score), 0)::INTEGER                     AS avg_score,
                    COALESCE(AVG(ss7.score), 0)::INTEGER                    AS avg_score_7d,
                    MAX(ss.scored_at)                                        AS last_scored_at
                FROM portfolio_suppliers ps
                LEFT JOIN LATERAL (
                    SELECT score, risk_level, scored_at
                    FROM supplier_scores
                    WHERE supplier_id = ps.supplier_id
                    ORDER BY score_date DESC
                    LIMIT 1
                ) ss ON TRUE
                LEFT JOIN LATERAL (
                    SELECT score
                    FROM supplier_scores
                    WHERE supplier_id = ps.supplier_id
                      AND score_date <= CURRENT_DATE - INTERVAL '7 days'
                    ORDER BY score_date DESC
                    LIMIT 1
                ) ss7 ON TRUE
                WHERE ps.tenant_id = $1::uuid
                """,
                tenant_id,
            )

            unread_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM alerts
                WHERE tenant_id = $1::uuid AND read_at IS NULL
                  AND status NOT IN ('resolved', 'dismissed')
                """,
                tenant_id,
            )

        avg_now = stats["avg_score"] or 0
        avg_7d = stats["avg_score_7d"] or avg_now
        diff = avg_now - avg_7d
        if diff <= -2:
            trend = "improving"
        elif diff >= 2:
            trend = "worsening"
        else:
            trend = "stable"

        total = stats["total_suppliers"] or 0

        return PortfolioSummaryData(
            total_suppliers=total,
            high_risk_count=stats["high_risk_count"] or 0,
            medium_risk_count=stats["medium_risk_count"] or 0,
            low_risk_count=stats["low_risk_count"] or 0,
            unread_alerts_count=unread_count or 0,
            average_portfolio_score=avg_now,
            score_trend_7d=trend,
            last_scored_at=stats["last_scored_at"],
            plan_supplier_limit=plan_limit,
            plan_supplier_used=total,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_score(row: asyncpg.Record) -> SupplierScore:
    breakdown = row["signal_breakdown"]
    if isinstance(breakdown, str):
        breakdown = json.loads(breakdown)
    return SupplierScore(
        supplier_id=row["supplier_id"],
        score=row["score"],
        risk_level=row["risk_level"],
        score_date=row["score_date"],
        signal_breakdown=breakdown or {},
        model_version=row["model_version"],
        data_completeness=row["data_completeness"],
        scored_at=row["scored_at"],
    )
