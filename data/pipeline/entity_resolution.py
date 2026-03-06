"""Supplier entity resolution pipeline.

Maps raw company name strings to canonical supplier IDs.

Three-stage pipeline (see DATA_SOURCES.md Section 8):
    Stage 1: Exact match after normalisation (~60% of cases, confidence 1.0)
    Stage 2: Fuzzy match via rapidfuzz token_sort_ratio >= 85 (~25% of cases)
    Stage 3: LLM-assisted via GPT-4o-mini for hard cases, scores 70–84 (~10% of cases)

Unresolved entities written to pipeline.unresolved_entities for manual review.
Never raises — unresolved is a valid outcome.
Every resolution is logged with method + confidence score.
"""

import asyncio
import json
import string
from datetime import datetime, timezone
from typing import Literal, Protocol

import httpx
import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from rapidfuzz import fuzz

from data.pipeline.models import ResolutionResult, SupplierRegistryEntry, UnresolvedEntity

log = structlog.get_logger()

# ── Legal suffix stripping ────────────────────────────────────────────────────

LEGAL_SUFFIXES: list[str] = [
    # English
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "lp",
    "corp", "corporation", "co", "company", "plc",
    "holdings", "holding", "group", "international", "global",
    "enterprises", "ventures", "industries", "solutions", "technologies",
    # German
    "ag", "gmbh", "kg", "ohg",
    # French
    "sa", "sas", "sarl", "sasu",
    # Dutch / Belgian
    "bv", "nv",
    # Nordic
    "ab", "oy", "as", "asa",
    # Japanese (romanised)
    "kk", "kabushiki kaisha",
]

_PUNCT_TABLE = str.maketrans(string.punctuation, " " * len(string.punctuation))
_SUFFIX_SET: frozenset[str] = frozenset(LEGAL_SUFFIXES)


# ── Settings ──────────────────────────────────────────────────────────────────

class EntityResolutionSettings(BaseSettings):
    llm_resolution_daily_limit: int = 200

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ── Registry Protocol + implementations ──────────────────────────────────────

class SupplierRegistry(Protocol):
    """Interface for supplier canonical data. Swappable for testing."""

    async def get_all(self) -> list[SupplierRegistryEntry]: ...
    async def get_by_id(self, supplier_id: str) -> SupplierRegistryEntry | None: ...
    async def search_by_name(self, name: str) -> list[SupplierRegistryEntry]: ...
    async def add_unresolved(self, entity: UnresolvedEntity) -> None: ...


class InMemorySupplierRegistry:
    """Test implementation. Initialise with a list of SupplierRegistryEntry."""

    def __init__(self, entries: list[SupplierRegistryEntry]) -> None:
        self._entries = list(entries)
        self._unresolved: list[UnresolvedEntity] = []

    async def get_all(self) -> list[SupplierRegistryEntry]:
        return list(self._entries)

    async def get_by_id(self, supplier_id: str) -> SupplierRegistryEntry | None:
        return next((e for e in self._entries if e.supplier_id == supplier_id), None)

    async def search_by_name(self, name: str) -> list[SupplierRegistryEntry]:
        lower = name.lower()
        return [
            e for e in self._entries
            if lower in e.canonical_name.lower()
            or any(lower in alias.lower() for alias in e.aliases)
        ]

    async def add_unresolved(self, entity: UnresolvedEntity) -> None:
        self._unresolved.append(entity)

    @property
    def unresolved(self) -> list[UnresolvedEntity]:
        return list(self._unresolved)


class PostgresSupplierRegistry:
    """Production implementation backed by Postgres pipeline.supplier_registry table."""

    def __init__(self, pool: object) -> None:  # asyncpg.Pool
        self._pool = pool

    async def get_all(self) -> list[SupplierRegistryEntry]:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            rows = await conn.fetch(
                "SELECT * FROM pipeline.supplier_registry ORDER BY canonical_name"
            )
            return [SupplierRegistryEntry(**dict(row)) for row in rows]

    async def get_by_id(self, supplier_id: str) -> SupplierRegistryEntry | None:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            row = await conn.fetchrow(
                "SELECT * FROM pipeline.supplier_registry WHERE supplier_id = $1",
                supplier_id,
            )
            return SupplierRegistryEntry(**dict(row)) if row else None

    async def search_by_name(self, name: str) -> list[SupplierRegistryEntry]:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            rows = await conn.fetch(
                "SELECT * FROM pipeline.supplier_registry "
                "WHERE canonical_name ILIKE $1 OR $1 = ANY(aliases)",
                f"%{name}%",
            )
            return [SupplierRegistryEntry(**dict(row)) for row in rows]

    async def add_unresolved(self, entity: UnresolvedEntity) -> None:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            await conn.execute(
                """
                INSERT INTO pipeline.unresolved_entities
                    (raw_name, country_hint, source, context, attempted_at, attempts)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (raw_name, source) DO UPDATE
                    SET attempts = pipeline.unresolved_entities.attempts + 1,
                        attempted_at = EXCLUDED.attempted_at
                """,
                entity.raw_name,
                entity.country_hint,
                entity.source,
                entity.context,
                entity.attempted_at,
                entity.attempts,
            )


# ── LLM Client Protocol + implementation ─────────────────────────────────────

class LLMClient(Protocol):
    """Interface for LLM-assisted entity resolution. Swappable for testing."""

    async def is_same_company(
        self,
        raw_name: str,
        candidate_name: str,
        context: str | None,
    ) -> tuple[bool, float]: ...


class OpenAILLMClient:
    """GPT-4o-mini client for entity resolution hard cases (~$0.0001 per call)."""

    _ENDPOINT = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _build_prompt(
        self, raw_name: str, candidate_name: str, context: str | None
    ) -> str:
        return (
            f"Is '{raw_name}' the same company as '{candidate_name}'?\n"
            f"Additional context: {context or 'none'}\n"
            'Answer with JSON only: '
            '{"match": true/false, "confidence": 0.0-1.0, "reason": "brief"}'
        )

    async def is_same_company(
        self,
        raw_name: str,
        candidate_name: str,
        context: str | None,
    ) -> tuple[bool, float]:
        prompt = self._build_prompt(raw_name, candidate_name, context)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._ENDPOINT,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=30.0,
            )
            response.raise_for_status()
        content = json.loads(response.json()["choices"][0]["message"]["content"])
        return bool(content["match"]), float(content["confidence"])


# ── Entity Resolver ───────────────────────────────────────────────────────────

# Maps normalised string → (registry entry, method used to index it)
_IndexEntry = tuple[SupplierRegistryEntry, Literal["exact", "alias"]]


class EntityResolver:
    """Three-stage supplier entity resolution pipeline.

    Stage 1 — Exact match after normalisation (confidence 1.0).
    Stage 2 — rapidfuzz token_sort_ratio, threshold 85 (80 with matching country hint).
    Stage 3 — GPT-4o-mini, only when Stage 2 scores 70–84.

    Never raises. Unresolved is a valid outcome — written to registry review queue.
    """

    def __init__(
        self,
        registry: SupplierRegistry,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._registry = registry
        self._llm_client = llm_client
        settings = EntityResolutionSettings()
        self._llm_daily_limit: int = settings.llm_resolution_daily_limit
        self._llm_calls_today: int = 0
        self._exact_index: dict[str, _IndexEntry] = {}
        self._fuzzy_index: list[tuple[str, SupplierRegistryEntry]] = []
        self._index_ready: bool = False

    # ── Index management ──────────────────────────────────────────────────────

    async def _ensure_index(self) -> None:
        if not self._index_ready:
            entries = await self._registry.get_all()
            for entry in entries:
                self._index_entry(entry)
            self._index_ready = True

    def _index_entry(self, entry: SupplierRegistryEntry) -> None:
        canonical_norm = self._normalise(entry.canonical_name)
        if canonical_norm:
            self._exact_index[canonical_norm] = (entry, "exact")
            self._fuzzy_index.append((canonical_norm, entry))
        for alias in entry.aliases:
            alias_norm = self._normalise(alias)
            if alias_norm and alias_norm not in self._exact_index:
                self._exact_index[alias_norm] = (entry, "alias")
                self._fuzzy_index.append((alias_norm, entry))

    # ── Normalisation ─────────────────────────────────────────────────────────

    def _normalise(self, name: str) -> str:
        """Lowercase, strip ASCII punctuation, remove legal suffixes, collapse whitespace.

        Non-ASCII characters (e.g. CJK) are preserved unchanged.
        """
        lowered = name.lower()
        no_punct = lowered.translate(_PUNCT_TABLE)
        words = [w for w in no_punct.split() if w not in _SUFFIX_SET]
        return " ".join(words).strip()

    # ── Result constructors ───────────────────────────────────────────────────

    def _resolved_result(
        self,
        raw_name: str,
        country_hint: str | None,
        entry: SupplierRegistryEntry,
        confidence: float,
        method: str,
        matched_string: str,
    ) -> ResolutionResult:
        return ResolutionResult(
            raw_name=raw_name,
            country_hint=country_hint,
            resolved=True,
            supplier_id=entry.supplier_id,
            canonical_name=entry.canonical_name,
            confidence=confidence,
            method=method,  # type: ignore[arg-type]
            matched_string=matched_string,
            resolved_at=datetime.now(timezone.utc),
        )

    def _unresolved_result(
        self, raw_name: str, country_hint: str | None
    ) -> ResolutionResult:
        return ResolutionResult(
            raw_name=raw_name,
            country_hint=country_hint,
            resolved=False,
            confidence=0.0,
            method="unresolved",
            resolved_at=datetime.now(timezone.utc),
        )

    # ── Stage 1: Exact match ──────────────────────────────────────────────────

    def _stage1_exact(
        self, normalised: str, raw_name: str, country_hint: str | None
    ) -> ResolutionResult | None:
        hit = self._exact_index.get(normalised)
        if hit is None:
            return None
        entry, method = hit
        result = self._resolved_result(
            raw_name, country_hint, entry, 1.0, method, normalised
        )
        log.info(
            "entity_resolution.stage1_match",
            raw_name=raw_name,
            supplier_id=entry.supplier_id,
            method=method,
        )
        return result

    # ── Stage 2: Fuzzy match ──────────────────────────────────────────────────

    def _stage2_fuzzy(
        self,
        normalised: str,
        raw_name: str,
        country_hint: str | None,
    ) -> tuple[ResolutionResult | None, list[tuple[float, SupplierRegistryEntry, str]]]:
        """Returns (match_result | None, stage3_candidates).

        Threshold is 85, lowered to 80 when country_hint matches the entry's country.
        Candidates scoring 70–84 (and not meeting the threshold) go to Stage 3.
        """
        best_score: float = 0.0
        best_entry: SupplierRegistryEntry | None = None
        best_norm = ""
        best_country_match = False
        stage3_candidates: list[tuple[float, SupplierRegistryEntry, str]] = []

        for candidate_norm, entry in self._fuzzy_index:
            score = fuzz.token_sort_ratio(normalised, candidate_norm)
            country_matches = country_hint is not None and entry.country == country_hint
            is_match = score >= 85 or (country_matches and score >= 80)

            if is_match:
                if score > best_score or (
                    score == best_score and country_matches and not best_country_match
                ):
                    best_score, best_entry, best_norm = score, entry, candidate_norm
                    best_country_match = country_matches
            elif score >= 70:
                stage3_candidates.append((score, entry, candidate_norm))

        if best_entry is not None:
            result = self._resolved_result(
                raw_name, country_hint, best_entry, best_score / 100.0, "fuzzy", best_norm
            )
            log.info(
                "entity_resolution.stage2_match",
                raw_name=raw_name,
                supplier_id=best_entry.supplier_id,
                score=best_score,
            )
            return result, []

        stage3_candidates.sort(key=lambda x: x[0], reverse=True)
        return None, stage3_candidates[:3]

    # ── Stage 3: LLM-assisted ─────────────────────────────────────────────────

    async def _stage3_llm(
        self,
        raw_name: str,
        country_hint: str | None,
        candidates: list[tuple[float, SupplierRegistryEntry, str]],
        context: str | None,
    ) -> ResolutionResult | None:
        if not self._llm_client or not candidates:
            return None
        if self._llm_calls_today >= self._llm_daily_limit:
            log.warning(
                "entity_resolution.llm_daily_limit_reached",
                raw_name=raw_name,
                calls_today=self._llm_calls_today,
                limit=self._llm_daily_limit,
            )
            return None

        for _score, entry, candidate_norm in candidates:
            self._llm_calls_today += 1
            try:
                matched, confidence = await self._llm_client.is_same_company(
                    raw_name, entry.canonical_name, context
                )
            except Exception:
                log.exception("entity_resolution.llm_error", raw_name=raw_name)
                continue
            if matched:
                result = self._resolved_result(
                    raw_name, country_hint, entry, confidence, "llm", candidate_norm
                )
                log.info(
                    "entity_resolution.stage3_match",
                    raw_name=raw_name,
                    supplier_id=entry.supplier_id,
                    confidence=confidence,
                )
                return result

        return None

    # ── Unresolved handling ───────────────────────────────────────────────────

    async def _write_unresolved(
        self,
        raw_name: str,
        country_hint: str | None,
        source: str,
        context: str | None,
    ) -> None:
        entity = UnresolvedEntity(
            raw_name=raw_name,
            country_hint=country_hint,
            source=source,
            context=context,
            attempted_at=datetime.now(timezone.utc),
        )
        await self._registry.add_unresolved(entity)

    # ── Public API ────────────────────────────────────────────────────────────

    async def resolve(
        self,
        raw_name: str,
        country_hint: str | None = None,
        context: str | None = None,
        source: str = "unknown",
    ) -> ResolutionResult:
        """Resolve a raw company name to a canonical supplier ID.

        Runs Stage 1 → Stage 2 → Stage 3 in sequence.
        Returns ResolutionResult(resolved=False) if all stages fail.
        Never raises.
        """
        await self._ensure_index()
        normalised = self._normalise(raw_name)

        result = self._stage1_exact(normalised, raw_name, country_hint)
        if result:
            return result

        result, stage3_candidates = self._stage2_fuzzy(normalised, raw_name, country_hint)
        if result:
            return result

        if stage3_candidates:
            result = await self._stage3_llm(raw_name, country_hint, stage3_candidates, context)
            if result:
                return result

        await self._write_unresolved(raw_name, country_hint, source, context)
        log.info("entity_resolution.unresolved", raw_name=raw_name, source=source)
        return self._unresolved_result(raw_name, country_hint)

    async def resolve_batch(
        self,
        names: list[tuple[str, str | None]],
        max_concurrent: int = 10,
    ) -> list[ResolutionResult]:
        """Resolve multiple names concurrently.

        Uses asyncio.Semaphore to bound concurrency. One failure does not
        block others — individual errors are captured in ResolutionResult.
        """
        await self._ensure_index()
        semaphore = asyncio.Semaphore(max_concurrent)

        async def resolve_one(name: str, country_hint: str | None) -> ResolutionResult:
            async with semaphore:
                return await self.resolve(name, country_hint)

        tasks = [resolve_one(name, hint) for name, hint in names]
        return list(await asyncio.gather(*tasks))
