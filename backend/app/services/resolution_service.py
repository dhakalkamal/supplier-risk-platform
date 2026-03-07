"""Entity resolution service for POST /api/v1/suppliers/resolve.

Performs a lightweight two-stage lookup against the suppliers table:
  Stage 1 — Case-insensitive exact match on canonical_name
  Stage 2 — pg_trgm similarity for fuzzy alternatives (if extension available)

This is a SQL-only resolver sufficient for the API endpoint. The full 3-stage
pipeline (Kafka + GPT-4o-mini) from Session 3 handles production ingestion.
"""

from __future__ import annotations

import asyncpg
import structlog

from backend.app.models.requests import ResolveSupplierRequest
from backend.app.models.responses import ResolveAlternative, ResolveSupplierResponse

log = structlog.get_logger()


async def resolve_supplier(
    request: ResolveSupplierRequest,
    pool: asyncpg.Pool,
) -> ResolveSupplierResponse:
    """Resolve a raw company name to a canonical supplier record.

    Returns ResolveSupplierResponse with resolved=True on exact match,
    or resolved=False with fuzzy alternatives when no exact match exists.
    """
    async with pool.acquire() as conn:
        exact = await _exact_match(conn, request)
        if exact is not None:
            return exact

        alternatives = await _fuzzy_alternatives(conn, request)

    return ResolveSupplierResponse(
        resolved=False,
        supplier_id=None,
        canonical_name=None,
        country=None,
        confidence=0.0,
        match_method="no_match",
        alternatives=alternatives,
    )


async def _exact_match(
    conn: asyncpg.Connection,
    request: ResolveSupplierRequest,
) -> ResolveSupplierResponse | None:
    """Try a case-insensitive exact match on canonical_name."""
    if request.country_hint:
        row = await conn.fetchrow(
            "SELECT id, canonical_name, country "
            "FROM suppliers "
            "WHERE canonical_name ILIKE $1 AND country = $2 "
            "LIMIT 1",
            request.name,
            request.country_hint,
        )
    else:
        row = await conn.fetchrow(
            "SELECT id, canonical_name, country "
            "FROM suppliers "
            "WHERE canonical_name ILIKE $1 "
            "LIMIT 1",
            request.name,
        )

    if row is None:
        return None

    return ResolveSupplierResponse(
        resolved=True,
        supplier_id=row["id"],
        canonical_name=row["canonical_name"],
        country=row["country"],
        confidence=1.0,
        match_method="exact",
        alternatives=[],
    )


async def _fuzzy_alternatives(
    conn: asyncpg.Connection,
    request: ResolveSupplierRequest,
) -> list[ResolveAlternative]:
    """Return up to 5 candidates using pg_trgm similarity.

    Falls back to an empty list if pg_trgm is not installed.
    """
    try:
        if request.country_hint:
            rows = await conn.fetch(
                "SELECT id, canonical_name, country, "
                "similarity(canonical_name, $1) AS sim "
                "FROM suppliers "
                "WHERE country = $2 "
                "ORDER BY sim DESC "
                "LIMIT 5",
                request.name,
                request.country_hint,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, canonical_name, country, "
                "similarity(canonical_name, $1) AS sim "
                "FROM suppliers "
                "ORDER BY sim DESC "
                "LIMIT 5",
                request.name,
            )
    except asyncpg.UndefinedFunctionError:
        log.debug("resolution.trgm_unavailable", name=request.name)
        return []

    return [
        ResolveAlternative(
            supplier_id=r["id"],
            canonical_name=r["canonical_name"],
            country=r["country"],
            confidence=round(float(r["sim"]), 3),
        )
        for r in rows
        if r["sim"] > 0.1
    ]
