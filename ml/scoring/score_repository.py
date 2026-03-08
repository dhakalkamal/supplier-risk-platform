"""Score repository — storage layer for daily supplier risk scores.

Three implementations following the project repository pattern (ADR-010):
  ScoreRepository         — Protocol (interface)
  InMemoryScoreRepository — for tests, no database required
  PostgresScoreRepository — production, writes to public.supplier_scores

Inject via FastAPI Depends() or pass directly in Airflow tasks.
Never instantiate PostgresScoreRepository directly in business logic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Protocol, runtime_checkable

import structlog

from ml.scoring.models import DailyScoreRecord

log = structlog.get_logger(__name__)

_TABLE = "supplier_scores"  # public schema — created by 001_initial_schema.sql


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class ScoreRepository(Protocol):
    """Interface for persisting and querying daily supplier risk scores.

    Never depend on a concrete implementation — always type-hint against
    this Protocol so the Postgres implementation can be swapped in tests.
    """

    async def upsert_daily_score(self, record: DailyScoreRecord) -> None:
        """Insert or replace the score for (supplier_id, feature_date)."""
        ...

    async def get_latest_score(self, supplier_id: str) -> DailyScoreRecord | None:
        """Return the most recent score for a supplier, or None if not yet scored."""
        ...

    async def get_score_history(
        self, supplier_id: str, days: int = 90
    ) -> list[DailyScoreRecord]:
        """Return daily scores for a supplier over the last `days` calendar days.

        Results are ordered by feature_date descending (most recent first).
        Returns an empty list if the supplier has no score history.
        """
        ...


# ── InMemory implementation (tests) ───────────────────────────────────────────


class InMemoryScoreRepository:
    """In-memory ScoreRepository for use in tests. No database required.

    Stores records in a dict keyed by (supplier_id, feature_date).
    upsert_daily_score overwrites the existing record for that key.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, date], DailyScoreRecord] = {}

    async def upsert_daily_score(self, record: DailyScoreRecord) -> None:
        """Upsert score keyed by (supplier_id, feature_date)."""
        self._store[(record.supplier_id, record.feature_date)] = record
        log.debug("memory_repo.score_upserted",
                  supplier_id=record.supplier_id, feature_date=str(record.feature_date))

    async def get_latest_score(self, supplier_id: str) -> DailyScoreRecord | None:
        """Return the most recent score for a supplier, or None."""
        candidates = [r for r in self._store.values() if r.supplier_id == supplier_id]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.feature_date)

    async def get_score_history(
        self, supplier_id: str, days: int = 90
    ) -> list[DailyScoreRecord]:
        """Return scores for the last `days` days, newest first."""
        cutoff = date.today() - timedelta(days=days)
        results = [
            r for r in self._store.values()
            if r.supplier_id == supplier_id and r.feature_date >= cutoff
        ]
        return sorted(results, key=lambda r: r.feature_date, reverse=True)

    def all_scores(self) -> list[DailyScoreRecord]:
        """Return all stored scores. Convenience method for test assertions."""
        return list(self._store.values())


# ── Postgres implementation (production) ──────────────────────────────────────


class PostgresScoreRepository:
    """Production ScoreRepository backed by Postgres scores schema.

    Writes to public.supplier_scores via asyncpg.
    Requires an asyncpg connection pool — inject via constructor.

    The upsert is keyed on (supplier_id, feature_date): one score per supplier
    per day. Re-scoring the same day overwrites the previous result.

    Args:
        pool: An asyncpg connection pool. Caller is responsible for lifecycle.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def upsert_daily_score(self, record: DailyScoreRecord) -> None:
        """Upsert a DailyScoreRecord into supplier_scores.

        The DB column is score_date; the Python model uses feature_date for the
        same concept. The mapping is explicit here so the rest of the codebase
        never needs to care about the column name difference.
        """
        query = f"""
            INSERT INTO {_TABLE} (
                id, supplier_id, score, risk_level,
                signal_breakdown, model_version, score_date,
                data_completeness, scored_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (supplier_id, score_date) DO UPDATE SET
                id               = EXCLUDED.id,
                score            = EXCLUDED.score,
                risk_level       = EXCLUDED.risk_level,
                signal_breakdown = EXCLUDED.signal_breakdown,
                model_version    = EXCLUDED.model_version,
                data_completeness = EXCLUDED.data_completeness,
                scored_at        = EXCLUDED.scored_at
        """
        # data_completeness is stored inside signal_breakdown (from RiskScoreOutput)
        data_completeness: float = record.signal_breakdown.get(  # type: ignore[assignment]
            "data_completeness", 0.0
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                query,
                record.id,
                record.supplier_id,
                record.score,
                record.risk_level,
                json.dumps(record.signal_breakdown),
                record.model_version,
                record.feature_date,   # feature_date → score_date column
                data_completeness,
                record.scored_at,
            )
        log.info("postgres_repo.score_upserted",
                 supplier_id=record.supplier_id, score=record.score,
                 feature_date=str(record.feature_date))

    async def get_latest_score(self, supplier_id: str) -> DailyScoreRecord | None:
        """Return the most recent score for a supplier, or None."""
        query = f"""
            SELECT id, supplier_id, score, risk_level,
                   signal_breakdown, model_version, score_date, data_completeness, scored_at
            FROM {_TABLE}
            WHERE supplier_id = $1
            ORDER BY score_date DESC
            LIMIT 1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, supplier_id)
        if row is None:
            return None
        return _row_to_record(row)

    async def get_score_history(
        self, supplier_id: str, days: int = 90
    ) -> list[DailyScoreRecord]:
        """Return scores for the last `days` calendar days, newest first."""
        cutoff = date.today() - timedelta(days=days)
        query = f"""
            SELECT id, supplier_id, score, risk_level,
                   signal_breakdown, model_version, score_date, data_completeness, scored_at
            FROM {_TABLE}
            WHERE supplier_id = $1
              AND score_date >= $2
            ORDER BY score_date DESC
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, supplier_id, cutoff)
        return [_row_to_record(row) for row in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_record(row: Any) -> DailyScoreRecord:
    """Deserialize an asyncpg Record row into a DailyScoreRecord.

    The DB column is score_date; mapped to feature_date in the Python model.
    """
    breakdown = row["signal_breakdown"]
    if isinstance(breakdown, str):
        breakdown = json.loads(breakdown)
    return DailyScoreRecord.model_validate({
        "id":                 str(row["id"]),
        "supplier_id":        row["supplier_id"],
        "score":              row["score"],
        "risk_level":         row["risk_level"],
        "signal_breakdown":   breakdown,
        "model_version":      row["model_version"],
        "feature_date":       row["score_date"],   # score_date → feature_date
        "data_completeness":  row["data_completeness"] or 0.0,
        "scored_at":          row["scored_at"],
    })
