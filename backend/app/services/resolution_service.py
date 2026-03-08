"""Entity resolution service for POST /api/v1/suppliers/resolve.

Performs a three-stage lookup against the suppliers table:
  Stage 1 — Substring match: canonical_name ILIKE '%query%' (supports typeahead)
  Stage 2 — Alias match: any alias ILIKE '%query%'
  Stage 3 — pg_trgm trigram similarity (if extension available, as last resort)

Returns the best match as resolved=True when exactly one result is returned
with confidence 1.0, otherwise returns resolved=False with up to 5 alternatives.

This is the SQL-only resolver for the API endpoint. The full 3-stage
pipeline (Kafka + GPT-4o-mini) from Session 3 handles production ingestion.
"""

from __future__ import annotations

import asyncpg
import structlog

from backend.app.models.requests import ResolveSupplierRequest
from backend.app.models.responses import ResolveAlternative, ResolveSupplierResponse

log = structlog.get_logger()

_MAX_RESULTS = 5


async def resolve_supplier(
    request: ResolveSupplierRequest,
    pool: asyncpg.Pool,
) -> ResolveSupplierResponse:
    """Resolve a raw company name to a canonical supplier record.

    Returns ResolveSupplierResponse with resolved=True when a single high-
    confidence match is found, or resolved=False with fuzzy alternatives
    when multiple candidates exist.
    """
    async with pool.acquire() as conn:
        results = await _substring_search(conn, request)

        if not results:
            results = await _trigram_search(conn, request)

    if not results:
        return ResolveSupplierResponse(
            resolved=False,
            supplier_id=None,
            canonical_name=None,
            country=None,
            confidence=0.0,
            match_method="no_match",
            alternatives=[],
        )

    # Single exact match (full name typed) → resolved=True
    if len(results) == 1 and results[0]["confidence"] == 1.0:
        r = results[0]
        return ResolveSupplierResponse(
            resolved=True,
            supplier_id=r["id"],
            canonical_name=r["canonical_name"],
            country=r["country"],
            confidence=1.0,
            match_method="exact",
            alternatives=[],
        )

    # Multiple candidates → let caller choose from alternatives
    alternatives = [
        ResolveAlternative(
            supplier_id=r["id"],
            canonical_name=r["canonical_name"],
            country=r["country"],
            confidence=round(r["confidence"], 3),
        )
        for r in results
    ]
    return ResolveSupplierResponse(
        resolved=False,
        supplier_id=None,
        canonical_name=None,
        country=None,
        confidence=0.0,
        match_method="substring",
        alternatives=alternatives,
    )


async def _substring_search(
    conn: asyncpg.Connection,
    request: ResolveSupplierRequest,
) -> list[dict]:
    """Search canonical_name and aliases using ILIKE '%query%'.

    Exact full-name match scores 1.0; substring matches score 0.8.
    Results are sorted: exact first, then alphabetically.
    """
    pattern = f"%{request.name}%"

    if request.country_hint:
        rows = await conn.fetch(
            """
            SELECT id, canonical_name, country,
                   CASE WHEN canonical_name ILIKE $1 THEN 1.0 ELSE 0.8 END AS confidence
            FROM suppliers
            WHERE country = $3
              AND (
                  canonical_name ILIKE $2
                  OR EXISTS (
                      SELECT 1 FROM unnest(aliases) AS a WHERE a ILIKE $2
                  )
              )
            ORDER BY confidence DESC, canonical_name
            LIMIT $4
            """,
            request.name,   # $1 — exact match check
            pattern,        # $2 — substring pattern
            request.country_hint,
            _MAX_RESULTS,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id, canonical_name, country,
                   CASE WHEN canonical_name ILIKE $1 THEN 1.0 ELSE 0.8 END AS confidence
            FROM suppliers
            WHERE canonical_name ILIKE $2
               OR EXISTS (
                   SELECT 1 FROM unnest(aliases) AS a WHERE a ILIKE $2
               )
            ORDER BY confidence DESC, canonical_name
            LIMIT $3
            """,
            request.name,   # $1 — exact match check
            pattern,        # $2 — substring pattern
            _MAX_RESULTS,
        )

    return [dict(r) for r in rows]


async def _trigram_search(
    conn: asyncpg.Connection,
    request: ResolveSupplierRequest,
) -> list[dict]:
    """Trigram similarity fallback when no substring matches exist.

    Falls back to empty list if pg_trgm extension is not installed.
    """
    try:
        if request.country_hint:
            rows = await conn.fetch(
                """
                SELECT id, canonical_name, country,
                       similarity(canonical_name, $1) AS confidence
                FROM suppliers
                WHERE country = $2
                ORDER BY confidence DESC
                LIMIT $3
                """,
                request.name,
                request.country_hint,
                _MAX_RESULTS,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, canonical_name, country,
                       similarity(canonical_name, $1) AS confidence
                FROM suppliers
                ORDER BY confidence DESC
                LIMIT $2
                """,
                request.name,
                _MAX_RESULTS,
            )
    except asyncpg.UndefinedFunctionError:
        log.debug("resolution.trgm_unavailable", name=request.name)
        return []

    return [dict(r) for r in rows if r["confidence"] > 0.1]
