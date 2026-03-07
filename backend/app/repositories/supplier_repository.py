"""Supplier repository — portfolio CRUD and supplier profile lookups.

Protocol + InMemory + Postgres pattern (ADR-010).
Inject PostgresSupplierRepository via FastAPI Depends() — never instantiate directly.

ID conventions:
  suppliers.id         → stored in DB as 'sup_XXXX' (app-generated prefix)
  portfolio_suppliers.id → raw UUID in DB; returned as 'pf_<uuid_no_dashes>' in API
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, runtime_checkable

import asyncpg
import structlog
from pydantic import BaseModel

from backend.app.models.errors import (
    PortfolioSupplierNotFoundError,
    SupplierAlreadyInPortfolioError,
    SupplierNotFoundError,
)
from backend.app.models.requests import (
    AddSupplierRequest,
    PatchPortfolioSupplierRequest,
    PortfolioSuppliersParams,
)
from backend.app.models.responses import SupplierProfile, SupplierSummary

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain model returned by add_to_portfolio and patch_portfolio_supplier
# ---------------------------------------------------------------------------


class PortfolioSupplierRecord(BaseModel):
    """Internal domain record for a portfolio ↔ supplier relationship.

    Returned by repository methods; route handlers project this to the
    appropriate API response model (AddSupplierResponse / PatchPortfolioSupplierResponse).
    """

    portfolio_supplier_id: str  # 'pf_<uuid_no_dashes>'
    supplier_id: str
    tenant_id: str
    canonical_name: str
    custom_name: str | None
    internal_id: str | None
    tags: list[str]
    resolution_confidence: float | None
    resolution_method: str | None
    added_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# ID formatting helpers
# ---------------------------------------------------------------------------


def _pf_id(raw: Any) -> str:
    """Format a raw UUID as a 'pf_' prefixed portfolio supplier ID."""
    return "pf_" + str(raw).replace("-", "")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SupplierRepository(Protocol):
    """Contract for supplier and portfolio data access."""

    async def get_by_id(
        self,
        supplier_id: str,
        tenant_id: str | None = None,
    ) -> SupplierProfile | None:
        """Return a full supplier profile, or None if not found.

        tenant_id is optional — when provided, populates in_portfolio and
        portfolio_supplier_id relative to that tenant's portfolio.
        """
        ...

    async def get_portfolio_suppliers(
        self,
        tenant_id: str,
        params: PortfolioSuppliersParams,
    ) -> tuple[list[SupplierSummary], int]:
        """Return paginated portfolio rows plus total count."""
        ...

    async def add_to_portfolio(
        self,
        tenant_id: str,
        request: AddSupplierRequest,
    ) -> PortfolioSupplierRecord:
        """Add a supplier to the tenant's portfolio.

        Raises SupplierNotFoundError if supplier_id is unknown.
        Raises SupplierAlreadyInPortfolioError if already in portfolio.
        """
        ...

    async def remove_from_portfolio(
        self,
        tenant_id: str,
        portfolio_supplier_id: str,
    ) -> None:
        """Remove a supplier from the portfolio.

        Raises PortfolioSupplierNotFoundError if not found for this tenant.
        """
        ...

    async def patch_portfolio_supplier(
        self,
        tenant_id: str,
        portfolio_supplier_id: str,
        request: PatchPortfolioSupplierRequest,
    ) -> PortfolioSupplierRecord:
        """Update custom_name, internal_id, and/or tags for a portfolio entry.

        Raises PortfolioSupplierNotFoundError if not found for this tenant.
        """
        ...

    async def count_portfolio(self, tenant_id: str) -> int:
        """Return the number of suppliers in the tenant's portfolio."""
        ...


# ---------------------------------------------------------------------------
# InMemory implementation — for unit tests
# ---------------------------------------------------------------------------


class InMemorySupplierRepository:
    """In-memory supplier repository. Pre-populate with seed_supplier() / seed_portfolio().

    Not thread-safe — sufficient for synchronous pytest tests.
    """

    def __init__(self) -> None:
        # supplier_id → dict of supplier fields
        self._suppliers: dict[str, dict[str, Any]] = {}
        # portfolio_supplier_id → PortfolioSupplierRecord
        self._portfolio: dict[str, PortfolioSupplierRecord] = {}

    # -- Test helpers --------------------------------------------------------

    def seed_supplier(
        self,
        supplier_id: str,
        canonical_name: str,
        country: str = "US",
        **kwargs: Any,
    ) -> None:
        self._suppliers[supplier_id] = {
            "supplier_id": supplier_id,
            "canonical_name": canonical_name,
            "aliases": [],
            "country": country,
            "industry_code": None,
            "industry_name": None,
            "duns_number": None,
            "cik": None,
            "website": None,
            "is_public_company": False,
            "primary_location": None,
            **kwargs,
        }

    def seed_portfolio(self, record: PortfolioSupplierRecord) -> None:
        self._portfolio[record.portfolio_supplier_id] = record

    # -- Repository methods --------------------------------------------------

    async def get_by_id(
        self,
        supplier_id: str,
        tenant_id: str | None = None,
    ) -> SupplierProfile | None:
        row = self._suppliers.get(supplier_id)
        if row is None:
            return None

        in_portfolio = False
        portfolio_supplier_id = None
        if tenant_id is not None:
            for rec in self._portfolio.values():
                if rec.supplier_id == supplier_id and rec.tenant_id == tenant_id:
                    in_portfolio = True
                    portfolio_supplier_id = rec.portfolio_supplier_id
                    break

        return SupplierProfile(
            supplier_id=row["supplier_id"],
            canonical_name=row["canonical_name"],
            aliases=row.get("aliases", []),
            country=row["country"],
            industry_code=row.get("industry_code"),
            industry_name=row.get("industry_name"),
            duns_number=row.get("duns_number"),
            cik=row.get("cik"),
            website=row.get("website"),
            primary_location=row.get("primary_location"),
            is_public_company=row.get("is_public_company", False),
            in_portfolio=in_portfolio,
            portfolio_supplier_id=portfolio_supplier_id,
            current_score=None,
        )

    async def get_portfolio_suppliers(
        self,
        tenant_id: str,
        params: PortfolioSuppliersParams,
    ) -> tuple[list[SupplierSummary], int]:
        records = [r for r in self._portfolio.values() if r.tenant_id == tenant_id]

        if params.search:
            q = params.search.lower()
            records = [
                r
                for r in records
                if q in r.canonical_name.lower()
                or (r.custom_name and q in r.custom_name.lower())
            ]
        if params.tag:
            records = [r for r in records if params.tag in r.tags]

        total = len(records)
        start = (params.page - 1) * params.per_page
        page_records = records[start : start + params.per_page]

        summaries = [
            SupplierSummary(
                portfolio_supplier_id=r.portfolio_supplier_id,
                supplier_id=r.supplier_id,
                canonical_name=r.canonical_name,
                custom_name=r.custom_name,
                country=self._suppliers.get(r.supplier_id, {}).get("country", ""),
                industry_code=self._suppliers.get(r.supplier_id, {}).get("industry_code"),
                industry_name=self._suppliers.get(r.supplier_id, {}).get("industry_name"),
                internal_id=r.internal_id,
                tags=r.tags,
                risk_score=None,
                risk_level=None,
                score_7d_delta=None,
                score_trend=None,
                unread_alerts_count=0,
                last_score_updated_at=None,
                data_completeness=None,
                added_to_portfolio_at=r.added_at,
            )
            for r in page_records
        ]
        return summaries, total

    async def add_to_portfolio(
        self,
        tenant_id: str,
        request: AddSupplierRequest,
    ) -> PortfolioSupplierRecord:
        # Resolve supplier_id
        if request.supplier_id is not None:
            supplier_id = request.supplier_id
            if supplier_id not in self._suppliers:
                raise SupplierNotFoundError(supplier_id)
            confidence: float | None = None
            method: str | None = None
        else:
            raw_name = request.raw_name or ""
            match = next(
                (
                    (sid, row)
                    for sid, row in self._suppliers.items()
                    if row["canonical_name"].lower() == raw_name.lower()
                ),
                None,
            )
            if match is None:
                raise SupplierNotFoundError(raw_name)
            supplier_id, _ = match
            confidence = 1.0
            method = "exact"

        # Duplicate check
        for rec in self._portfolio.values():
            if rec.supplier_id == supplier_id and rec.tenant_id == tenant_id:
                raise SupplierAlreadyInPortfolioError(supplier_id)

        now = _now()
        pf_id = _pf_id(uuid.uuid4())
        canonical_name = self._suppliers[supplier_id]["canonical_name"]
        record = PortfolioSupplierRecord(
            portfolio_supplier_id=pf_id,
            supplier_id=supplier_id,
            tenant_id=tenant_id,
            canonical_name=canonical_name,
            custom_name=None,
            internal_id=request.internal_id,
            tags=request.tags,
            resolution_confidence=confidence,
            resolution_method=method,
            added_at=now,
            updated_at=now,
        )
        self._portfolio[pf_id] = record
        return record

    async def remove_from_portfolio(
        self,
        tenant_id: str,
        portfolio_supplier_id: str,
    ) -> None:
        rec = self._portfolio.get(portfolio_supplier_id)
        if rec is None or rec.tenant_id != tenant_id:
            raise PortfolioSupplierNotFoundError(portfolio_supplier_id)
        del self._portfolio[portfolio_supplier_id]

    async def patch_portfolio_supplier(
        self,
        tenant_id: str,
        portfolio_supplier_id: str,
        request: PatchPortfolioSupplierRequest,
    ) -> PortfolioSupplierRecord:
        rec = self._portfolio.get(portfolio_supplier_id)
        if rec is None or rec.tenant_id != tenant_id:
            raise PortfolioSupplierNotFoundError(portfolio_supplier_id)

        updated = rec.model_copy(
            update={
                k: v
                for k, v in {
                    "custom_name": request.custom_name,
                    "internal_id": request.internal_id,
                    "tags": request.tags,
                    "updated_at": _now(),
                }.items()
                if v is not None
            }
        )
        self._portfolio[portfolio_supplier_id] = updated
        return updated

    async def count_portfolio(self, tenant_id: str) -> int:
        return sum(1 for r in self._portfolio.values() if r.tenant_id == tenant_id)


# ---------------------------------------------------------------------------
# Postgres implementation — production
# ---------------------------------------------------------------------------


class PostgresSupplierRepository:
    """Production supplier repository backed by asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_id(
        self,
        supplier_id: str,
        tenant_id: str | None = None,
    ) -> SupplierProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    s.id              AS supplier_id,
                    s.canonical_name,
                    s.aliases,
                    s.country,
                    s.industry_code,
                    s.industry_name,
                    s.duns_number,
                    s.cik,
                    s.website,
                    s.is_public_company,
                    s.primary_location,
                    ps.id             AS portfolio_supplier_uuid,
                    ss.score,
                    ss.risk_level,
                    ss.model_version,
                    ss.scored_at,
                    ss.data_completeness,
                    ss.signal_breakdown
                FROM suppliers s
                LEFT JOIN LATERAL (
                    SELECT id FROM portfolio_suppliers
                    WHERE supplier_id = s.id AND tenant_id = $2
                    LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT score, risk_level, model_version, scored_at,
                           data_completeness, signal_breakdown
                    FROM supplier_scores
                    WHERE supplier_id = s.id
                    ORDER BY score_date DESC
                    LIMIT 1
                ) ss ON TRUE
                WHERE s.id = $1
                """,
                supplier_id,
                tenant_id,
            )

        if row is None:
            return None

        in_portfolio = row["portfolio_supplier_uuid"] is not None
        portfolio_supplier_id = (
            _pf_id(row["portfolio_supplier_uuid"]) if in_portfolio else None
        )

        return SupplierProfile(
            supplier_id=row["supplier_id"],
            canonical_name=row["canonical_name"],
            aliases=list(row["aliases"] or []),
            country=row["country"],
            industry_code=row["industry_code"],
            industry_name=row["industry_name"],
            duns_number=row["duns_number"],
            cik=row["cik"],
            website=row["website"],
            primary_location=row["primary_location"],
            is_public_company=row["is_public_company"],
            in_portfolio=in_portfolio,
            portfolio_supplier_id=portfolio_supplier_id,
            current_score=None,  # enriched by ScoreRepository in the route handler
        )

    async def get_portfolio_suppliers(
        self,
        tenant_id: str,
        params: PortfolioSuppliersParams,
    ) -> tuple[list[SupplierSummary], int]:
        sort_col = {
            "risk_score": "ss.score",
            "name": "COALESCE(ps.custom_name, s.canonical_name)",
            "last_updated": "ss.scored_at",
            "date_added": "ps.added_at",
        }[params.sort_by]
        sort_dir = "ASC" if params.sort_order == "asc" else "DESC"

        conditions = ["ps.tenant_id = $1"]
        args: list[Any] = [tenant_id]
        n = 2

        if params.risk_level:
            conditions.append(f"ss.risk_level = ${n}")
            args.append(params.risk_level)
            n += 1
        if params.country:
            conditions.append(f"s.country = ${n}")
            args.append(params.country)
            n += 1
        if params.search:
            conditions.append(
                f"(s.canonical_name ILIKE ${n} OR ps.custom_name ILIKE ${n})"
            )
            args.append(f"%{params.search}%")
            n += 1
        if params.tag:
            conditions.append(f"${n} = ANY(ps.tags)")
            args.append(params.tag)
            n += 1

        where = " AND ".join(conditions)
        offset = (params.page - 1) * params.per_page

        query = f"""
            SELECT
                ps.id             AS portfolio_supplier_uuid,
                s.id              AS supplier_id,
                s.canonical_name,
                ps.custom_name,
                s.country,
                s.industry_code,
                s.industry_name,
                ps.internal_id,
                ps.tags,
                ps.added_at,
                ss.score,
                ss.risk_level,
                ss.scored_at,
                ss.data_completeness,
                ss7.score         AS score_7d_ago,
                (SELECT COUNT(*) FROM alerts a
                 WHERE a.supplier_id = s.id AND a.tenant_id = ps.tenant_id
                   AND a.read_at IS NULL) AS unread_alerts_count,
                COUNT(*) OVER()   AS total_count
            FROM portfolio_suppliers ps
            JOIN suppliers s ON s.id = ps.supplier_id
            LEFT JOIN LATERAL (
                SELECT score, risk_level, scored_at, data_completeness
                FROM supplier_scores
                WHERE supplier_id = s.id
                ORDER BY score_date DESC
                LIMIT 1
            ) ss ON TRUE
            LEFT JOIN LATERAL (
                SELECT score
                FROM supplier_scores
                WHERE supplier_id = s.id
                  AND score_date <= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY score_date DESC
                LIMIT 1
            ) ss7 ON TRUE
            WHERE {where}
            ORDER BY {sort_col} {sort_dir} NULLS LAST
            LIMIT ${n} OFFSET ${n + 1}
        """
        args.extend([params.per_page, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)

        if not rows:
            return [], 0

        total = rows[0]["total_count"]
        summaries = []
        for row in rows:
            score = row["score"]
            score_7d = row["score_7d_ago"]
            delta = (score - score_7d) if (score is not None and score_7d is not None) else None
            trend: Literal["increasing", "decreasing", "stable"] | None = None
            if delta is not None:
                if delta > 0:
                    trend = "increasing"
                elif delta < 0:
                    trend = "decreasing"
                else:
                    trend = "stable"

            summaries.append(
                SupplierSummary(
                    portfolio_supplier_id=_pf_id(row["portfolio_supplier_uuid"]),
                    supplier_id=row["supplier_id"],
                    canonical_name=row["canonical_name"],
                    custom_name=row["custom_name"],
                    country=row["country"],
                    industry_code=row["industry_code"],
                    industry_name=row["industry_name"],
                    internal_id=row["internal_id"],
                    tags=list(row["tags"] or []),
                    risk_score=score,
                    risk_level=row["risk_level"],
                    score_7d_delta=delta,
                    score_trend=trend,
                    unread_alerts_count=row["unread_alerts_count"] or 0,
                    last_score_updated_at=row["scored_at"],
                    data_completeness=row["data_completeness"],
                    added_to_portfolio_at=row["added_at"],
                )
            )
        return summaries, total

    async def add_to_portfolio(
        self,
        tenant_id: str,
        request: AddSupplierRequest,
    ) -> PortfolioSupplierRecord:
        async with self._pool.acquire() as conn:
            # Resolve supplier_id
            if request.supplier_id is not None:
                supplier_id = request.supplier_id
                canonical_name = await conn.fetchval(
                    "SELECT canonical_name FROM suppliers WHERE id = $1", supplier_id
                )
                if canonical_name is None:
                    raise SupplierNotFoundError(supplier_id)
                confidence: float | None = None
                method: str | None = None
            else:
                raw_name = request.raw_name or ""
                row = await conn.fetchrow(
                    "SELECT id, canonical_name FROM suppliers "
                    "WHERE canonical_name ILIKE $1 LIMIT 1",
                    raw_name,
                )
                if row is None:
                    raise SupplierNotFoundError(raw_name)
                supplier_id = row["id"]
                canonical_name = row["canonical_name"]
                confidence = 1.0
                method = "exact"

            try:
                ps_row = await conn.fetchrow(
                    """
                    INSERT INTO portfolio_suppliers
                        (tenant_id, supplier_id, internal_id, tags)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, added_at
                    """,
                    uuid.UUID(tenant_id),
                    supplier_id,
                    request.internal_id,
                    request.tags or [],
                )
            except asyncpg.UniqueViolationError:
                raise SupplierAlreadyInPortfolioError(supplier_id)

        now = ps_row["added_at"]
        return PortfolioSupplierRecord(
            portfolio_supplier_id=_pf_id(ps_row["id"]),
            supplier_id=supplier_id,
            tenant_id=tenant_id,
            canonical_name=canonical_name,
            custom_name=None,
            internal_id=request.internal_id,
            tags=request.tags or [],
            resolution_confidence=confidence,
            resolution_method=method,
            added_at=now,
            updated_at=now,
        )

    async def remove_from_portfolio(
        self,
        tenant_id: str,
        portfolio_supplier_id: str,
    ) -> None:
        # Strip the 'pf_' prefix to get the raw UUID
        raw_uuid = portfolio_supplier_id.removeprefix("pf_")
        try:
            raw_id = uuid.UUID(raw_uuid)
        except ValueError:
            raise PortfolioSupplierNotFoundError(portfolio_supplier_id)

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM portfolio_suppliers WHERE id = $1 AND tenant_id = $2",
                raw_id,
                uuid.UUID(tenant_id),
            )
        if result == "DELETE 0":
            raise PortfolioSupplierNotFoundError(portfolio_supplier_id)

    async def patch_portfolio_supplier(
        self,
        tenant_id: str,
        portfolio_supplier_id: str,
        request: PatchPortfolioSupplierRequest,
    ) -> PortfolioSupplierRecord:
        raw_uuid = portfolio_supplier_id.removeprefix("pf_")
        try:
            raw_id = uuid.UUID(raw_uuid)
        except ValueError:
            raise PortfolioSupplierNotFoundError(portfolio_supplier_id)

        async with self._pool.acquire() as conn:
            # Always fetch current state first — needed for canonical_name and
            # as the authoritative existence / tenant check.
            existing = await conn.fetchrow(
                """
                SELECT ps.id, ps.supplier_id, ps.custom_name, ps.internal_id,
                       ps.tags, ps.added_at, s.canonical_name
                FROM portfolio_suppliers ps
                JOIN suppliers s ON s.id = ps.supplier_id
                WHERE ps.id = $1 AND ps.tenant_id = $2::uuid
                """,
                raw_id,
                uuid.UUID(tenant_id),
            )
            if existing is None:
                raise PortfolioSupplierNotFoundError(portfolio_supplier_id)

            updates: list[str] = []
            args: list[Any] = []
            n = 1

            if request.custom_name is not None:
                updates.append(f"custom_name = ${n}")
                args.append(request.custom_name)
                n += 1
            if request.internal_id is not None:
                updates.append(f"internal_id = ${n}")
                args.append(request.internal_id)
                n += 1
            if request.tags is not None:
                updates.append(f"tags = ${n}")
                args.append(request.tags)
                n += 1

            if updates:
                args.extend([raw_id, uuid.UUID(tenant_id)])
                await conn.execute(
                    f"UPDATE portfolio_suppliers SET {', '.join(updates)} "
                    f"WHERE id = ${n} AND tenant_id = ${n + 1}::uuid",
                    *args,
                )

        now = _now()
        return PortfolioSupplierRecord(
            portfolio_supplier_id=_pf_id(existing["id"]),
            supplier_id=existing["supplier_id"],
            tenant_id=tenant_id,
            canonical_name=existing["canonical_name"],
            custom_name=(
                request.custom_name
                if request.custom_name is not None
                else existing["custom_name"]
            ),
            internal_id=(
                request.internal_id
                if request.internal_id is not None
                else existing["internal_id"]
            ),
            tags=(
                request.tags
                if request.tags is not None
                else list(existing["tags"] or [])
            ),
            resolution_confidence=None,
            resolution_method=None,
            added_at=existing["added_at"],
            updated_at=now,
        )

    async def count_portfolio(self, tenant_id: str) -> int:
        async with self._pool.acquire() as conn:
            result: int = await conn.fetchval(
                "SELECT COUNT(*) FROM portfolio_suppliers WHERE tenant_id = $1",
                uuid.UUID(tenant_id),
            )
            return result
