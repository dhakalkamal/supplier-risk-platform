"""Tests for the entity resolution pipeline.

Coverage target: ≥85% on data/pipeline/entity_resolution.py.

Tests are organised by stage:
    - Normalisation
    - Stage 1: exact match (confidence 1.0)
    - Stage 2: fuzzy match (rapidfuzz token_sort_ratio)
    - Stage 3: LLM-assisted (mocked)
    - Unresolved handling
    - Batch resolution

All tests use InMemorySupplierRegistry — no database required.
LLM client is always mocked — no real API calls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from data.pipeline.entity_resolution import EntityResolver, InMemorySupplierRegistry
from data.pipeline.models import SupplierRegistryEntry, UnresolvedEntity

# ── Fixture helpers ───────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _entry(
    supplier_id: str,
    canonical_name: str,
    country: str,
    aliases: list[str] | None = None,
) -> SupplierRegistryEntry:
    return SupplierRegistryEntry(
        supplier_id=supplier_id,
        canonical_name=canonical_name,
        aliases=aliases or [],
        country=country,
        created_at=_NOW,
        updated_at=_NOW,
    )


# 20 real-ish supplier registry entries used across tests.
REGISTRY_ENTRIES: list[SupplierRegistryEntry] = [
    _entry("sup_apple", "Apple Inc", "US", ["AAPL", "Apple Computer"]),
    _entry("sup_tsmc",       "Taiwan Semiconductor Manufacturing Co", "TW", ["TSMC"]),
    _entry("sup_foxconn",    "Foxconn",                               "TW", [
        "Foxconn Technology Group", "Hon Hai Precision Industry",
    ]),
    _entry("sup_samsung",    "Samsung Electronics",                   "KR", ["Samsung"]),
    _entry("sup_nvidia",     "NVIDIA Corporation",                    "US", ["Nvidia"]),
    _entry("sup_intel",      "Intel Corporation",                     "US", ["Intel"]),
    _entry("sup_qualcomm",   "Qualcomm Incorporated",                 "US", ["QCOM", "Qualcomm"]),
    _entry("sup_bosch",      "Robert Bosch GmbH",                     "DE", ["Bosch"]),
    _entry("sup_basf",       "BASF SE",                               "DE", ["BASF"]),
    _entry("sup_siemens",    "Siemens AG",                            "DE", ["Siemens"]),
    _entry("sup_toyota",     "Toyota Motor Corporation",              "JP", ["Toyota"]),
    _entry("sup_hyundai",    "Hyundai Motor Company",                 "KR", ["Hyundai"]),
    _entry("sup_3m",         "3M Company",                            "US", [
        "MMM", "Minnesota Mining and Manufacturing",
    ]),
    _entry("sup_honeywell",  "Honeywell International Inc",           "US", ["Honeywell"]),
    _entry("sup_caterpillar","Caterpillar Inc",                       "US", ["CAT"]),
    _entry("sup_emerson",   "Emerson Electric Co",                    "US", ["Emerson"]),
    _entry("sup_parker",    "Parker Hannifin Corporation",            "US", ["Parker"]),
    _entry("sup_eaton",     "Eaton Corporation",                      "US", ["Eaton"]),
    _entry("sup_schneider", "Schneider Electric SE",                  "FR", ["Schneider"]),
    _entry("sup_abb",       "ABB Ltd",                                "CH", ["ABB Group"]),
]


@pytest.fixture
def registry() -> InMemorySupplierRegistry:
    return InMemorySupplierRegistry(REGISTRY_ENTRIES)


@pytest.fixture
def resolver(registry: InMemorySupplierRegistry) -> EntityResolver:
    return EntityResolver(registry=registry)


# ── Normalisation ─────────────────────────────────────────────────────────────

def test_normalise_strips_inc_suffix(resolver: EntityResolver) -> None:
    assert resolver._normalise("Apple Inc.") == "apple"


def test_normalise_strips_ltd_suffix(resolver: EntityResolver) -> None:
    assert resolver._normalise("ABB Ltd") == "abb"


def test_normalise_is_case_insensitive(resolver: EntityResolver) -> None:
    assert resolver._normalise("SAMSUNG ELECTRONICS CO., LTD") == "samsung electronics"


def test_normalise_preserves_non_ascii(resolver: EntityResolver) -> None:
    """CJK characters must survive normalisation unchanged."""
    assert resolver._normalise("台積電 Co., Ltd.") == "台積電"


def test_normalise_collapses_whitespace(resolver: EntityResolver) -> None:
    assert resolver._normalise("  Apple   Inc  ") == "apple"


def test_normalise_empty_after_suffix_removal(resolver: EntityResolver) -> None:
    """A name made entirely of suffixes collapses to empty string."""
    result = resolver._normalise("Inc Ltd Co")
    assert result == ""


# ── Stage 1: Exact match ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stage1_exact_match_canonical_name(resolver: EntityResolver) -> None:
    result = await resolver.resolve("Samsung Electronics")
    assert result.resolved is True
    assert result.supplier_id == "sup_samsung"
    assert result.confidence == 1.0
    assert result.method == "exact"


@pytest.mark.asyncio
async def test_stage1_exact_match_is_case_insensitive(resolver: EntityResolver) -> None:
    result = await resolver.resolve("samsung electronics")
    assert result.resolved is True
    assert result.supplier_id == "sup_samsung"


@pytest.mark.asyncio
async def test_stage1_apple_inc_normalises_to_apple(resolver: EntityResolver) -> None:
    """'Apple Inc' and 'Apple' should resolve to the same supplier after suffix removal."""
    result = await resolver.resolve("Apple Inc")
    assert result.resolved is True
    assert result.supplier_id == "sup_apple"
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_stage1_apple_inc_with_period(resolver: EntityResolver) -> None:
    """Trailing punctuation must not prevent an exact match."""
    result = await resolver.resolve("Apple Inc.")
    assert result.resolved is True
    assert result.supplier_id == "sup_apple"


@pytest.mark.asyncio
async def test_stage1_alias_match_tsmc(resolver: EntityResolver) -> None:
    """'TSMC' is a registered alias for Taiwan Semiconductor Manufacturing Co."""
    result = await resolver.resolve("TSMC")
    assert result.resolved is True
    assert result.supplier_id == "sup_tsmc"
    assert result.method == "alias"
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_stage1_alias_match_bosch(resolver: EntityResolver) -> None:
    result = await resolver.resolve("Bosch")
    assert result.resolved is True
    assert result.supplier_id == "sup_bosch"
    assert result.method == "alias"


# ── Stage 2: Fuzzy match ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stage2_single_char_typo_foxconn(resolver: EntityResolver) -> None:
    """'Foxcon' (missing 'n') should fuzzy-match 'Foxconn' with score ≥ 85."""
    result = await resolver.resolve("Foxcon")
    assert result.resolved is True
    assert result.supplier_id == "sup_foxconn"
    assert result.method == "fuzzy"
    assert result.confidence >= 0.85


@pytest.mark.asyncio
async def test_stage2_transposition_typo_samsung(resolver: EntityResolver) -> None:
    """'Samsumg Electronics' (transposition) should fuzzy-match Samsung Electronics."""
    result = await resolver.resolve("Samsumg Electronics")
    assert result.resolved is True
    assert result.supplier_id == "sup_samsung"
    assert result.method == "fuzzy"
    assert result.confidence >= 0.85


@pytest.mark.asyncio
async def test_stage2_unrelated_names_do_not_match(resolver: EntityResolver) -> None:
    """Completely unrelated names must not fuzzy-match any registry entry."""
    result = await resolver.resolve("Zyxeloptronics Kazimiera")
    assert result.resolved is False


@pytest.mark.asyncio
async def test_stage2_country_hint_lowers_threshold_match(
    resolver: EntityResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Score of 82 with matching country hint should resolve (threshold drops to 80)."""
    import data.pipeline.entity_resolution as er_module

    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 82)

    # Foxconn is country TW — country_hint="TW" activates the 80 threshold
    result = await resolver.resolve("Foxconn Precision", country_hint="TW")
    assert result.resolved is True
    assert result.method == "fuzzy"


@pytest.mark.asyncio
async def test_stage2_country_hint_no_match_without_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Score of 82 without country hint should NOT resolve (threshold stays at 85)."""
    import data.pipeline.entity_resolution as er_module

    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 82)

    fresh_resolver = EntityResolver(registry=InMemorySupplierRegistry(REGISTRY_ENTRIES))
    result = await fresh_resolver.resolve("Foxconn Precision")  # no country hint
    assert result.resolved is False


# ── Stage 3: LLM-assisted ─────────────────────────────────────────────────────

def _make_llm_mock(match: bool = True, confidence: float = 0.92) -> AsyncMock:
    """Return a mock LLMClient that returns (match, confidence)."""
    mock = AsyncMock()
    mock.is_same_company.return_value = (match, confidence)
    return mock


@pytest.mark.asyncio
async def test_stage3_llm_called_for_scores_70_to_84(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM must be invoked when Stage 2 produces candidates scoring 70–84."""
    import data.pipeline.entity_resolution as er_module

    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 77)

    llm_mock = _make_llm_mock(match=True, confidence=0.91)
    fresh_resolver = EntityResolver(
        registry=InMemorySupplierRegistry(REGISTRY_ENTRIES),
        llm_client=llm_mock,
    )
    # "Foxconn Systems" normalises to "foxconn systems" — not in exact index,
    # so Stage 1 is a miss and the monkeypatched score of 77 lands in Stage 3 range.
    result = await fresh_resolver.resolve("Foxconn Systems", context="supplier in Taiwan")

    llm_mock.is_same_company.assert_called()
    assert result.resolved is True
    assert result.method == "llm"


@pytest.mark.asyncio
async def test_stage3_llm_not_called_when_score_below_70(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 3 is skipped when the best fuzzy score is below 70 (too dissimilar)."""
    import data.pipeline.entity_resolution as er_module

    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 55)

    llm_mock = _make_llm_mock()
    fresh_resolver = EntityResolver(
        registry=InMemorySupplierRegistry(REGISTRY_ENTRIES),
        llm_client=llm_mock,
    )
    await fresh_resolver.resolve("Zyxeloptronics")

    llm_mock.is_same_company.assert_not_called()


@pytest.mark.asyncio
async def test_stage3_llm_not_called_when_stage2_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Stage 2 produces a match (score ≥ 85), Stage 3 must not run."""
    import data.pipeline.entity_resolution as er_module

    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 90)

    llm_mock = _make_llm_mock()
    fresh_resolver = EntityResolver(
        registry=InMemorySupplierRegistry(REGISTRY_ENTRIES),
        llm_client=llm_mock,
    )
    result = await fresh_resolver.resolve("Foxconn Systems")

    llm_mock.is_same_company.assert_not_called()
    assert result.resolved is True
    assert result.method == "fuzzy"


@pytest.mark.asyncio
async def test_stage3_llm_returning_false_gives_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LLM says match=False for all candidates, result must be unresolved."""
    import data.pipeline.entity_resolution as er_module

    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 77)

    llm_mock = _make_llm_mock(match=False, confidence=0.1)
    fresh_resolver = EntityResolver(
        registry=InMemorySupplierRegistry(REGISTRY_ENTRIES),
        llm_client=llm_mock,
    )
    result = await fresh_resolver.resolve("Foxconn Systems", context="supplier")

    assert result.resolved is False
    assert result.method == "unresolved"


@pytest.mark.asyncio
async def test_stage3_llm_daily_limit_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the daily call limit is reached, Stage 3 returns unresolved without calling the API."""
    import data.pipeline.entity_resolution as er_module

    # Force every fuzzy comparison into the 70–84 Stage 3 range
    monkeypatch.setattr(er_module.fuzz, "token_sort_ratio", lambda a, b: 77)

    llm_mock = _make_llm_mock(match=True, confidence=0.9)
    fresh_resolver = EntityResolver(
        registry=InMemorySupplierRegistry(REGISTRY_ENTRIES),
        llm_client=llm_mock,
    )
    fresh_resolver._llm_daily_limit = 2  # exhaust after 2 calls

    # First two resolutions consume the limit (each may call LLM once per candidate)
    await fresh_resolver.resolve("Foxconn Ltd A")
    await fresh_resolver.resolve("Foxconn Ltd B")

    # Reset call count tracker; manually exhaust the limit
    fresh_resolver._llm_calls_today = fresh_resolver._llm_daily_limit
    llm_mock.reset_mock()

    # This resolution should NOT call the LLM
    result = await fresh_resolver.resolve("Foxconn Ltd C")

    llm_mock.is_same_company.assert_not_called()
    assert result.resolved is False


# ── Unresolved handling ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unresolved_written_to_registry(registry: InMemorySupplierRegistry) -> None:
    """An unresolvable name must be written to the registry's unresolved queue."""
    fresh_resolver = EntityResolver(registry=registry)
    await fresh_resolver.resolve("Acme Widgets XYZ123", source="news")

    assert len(registry.unresolved) == 1
    entity: UnresolvedEntity = registry.unresolved[0]
    assert entity.raw_name == "Acme Widgets XYZ123"
    assert entity.source == "news"


@pytest.mark.asyncio
async def test_unresolved_returns_resolved_false(resolver: EntityResolver) -> None:
    result = await resolver.resolve("Acme Widgets XYZ123")
    assert result.resolved is False
    assert result.supplier_id is None
    assert result.canonical_name is None


@pytest.mark.asyncio
async def test_unresolved_does_not_raise(resolver: EntityResolver) -> None:
    """resolve() must never raise — unresolved is a valid outcome."""
    try:
        result = await resolver.resolve("💥🤖 definitely not a company 💥")
        assert result.resolved is False
    except Exception as exc:
        pytest.fail(f"resolve() raised unexpectedly: {exc}")


@pytest.mark.asyncio
async def test_unresolved_entity_captures_context(
    registry: InMemorySupplierRegistry,
) -> None:
    fresh_resolver = EntityResolver(registry=registry)
    ctx = "filed for Chapter 11 protection yesterday"
    await fresh_resolver.resolve("Acme Corp XYZ", context=ctx, source="sec")

    assert registry.unresolved[0].context == ctx
    assert registry.unresolved[0].source == "sec"


# ── Batch resolution ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_returns_result_for_every_input(resolver: EntityResolver) -> None:
    names = [
        ("Apple Inc", "US"),
        ("Samsung Electronics", "KR"),
        ("Acme Nonexistent", None),
        ("Foxconn", "TW"),
        ("TSMC", "TW"),
    ]
    results = await resolver.resolve_batch(names)

    assert len(results) == len(names)


@pytest.mark.asyncio
async def test_batch_one_failure_does_not_block_others(
    registry: InMemorySupplierRegistry,
) -> None:
    """A name that fails to resolve must not prevent the rest from resolving."""
    fresh_resolver = EntityResolver(registry=registry)
    names = [
        ("Apple Inc", "US"),
        ("Acme Nonexistent Corp 999", None),  # will be unresolved
        ("TSMC", "TW"),
    ]
    results = await fresh_resolver.resolve_batch(names)

    assert len(results) == 3
    assert results[0].resolved is True   # Apple
    assert results[1].resolved is False  # Acme
    assert results[2].resolved is True   # TSMC


@pytest.mark.asyncio
async def test_batch_uses_concurrent_execution() -> None:
    """resolve_batch must run resolutions concurrently, not sequentially.

    We track the peak number of in-flight resolutions. With asyncio.gather,
    all tasks are scheduled before any completes — peak should exceed 1.
    """
    active: int = 0
    peak_active: int = 0

    original_resolve = EntityResolver.resolve

    async def tracking_resolve(
        self: EntityResolver,
        name: str,
        country_hint: str | None = None,
        context: str | None = None,
        source: str = "unknown",
    ):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0)  # yield so other coroutines can start
        active -= 1
        return await original_resolve(
            self, name, country_hint=country_hint, context=context, source=source
        )

    registry = InMemorySupplierRegistry(REGISTRY_ENTRIES)
    fresh_resolver = EntityResolver(registry=registry)

    with patch.object(EntityResolver, "resolve", tracking_resolve):
        names = [("Apple Inc", "US")] * 8
        await fresh_resolver.resolve_batch(names, max_concurrent=8)

    assert peak_active > 1, "resolve_batch must execute concurrently, not sequentially"


@pytest.mark.asyncio
async def test_batch_respects_max_concurrent_limit() -> None:
    """max_concurrent cap must be honoured — peak active must not exceed it."""
    active: int = 0
    peak_active: int = 0

    original_resolve = EntityResolver.resolve

    async def tracking_resolve(
        self: EntityResolver,
        name: str,
        country_hint: str | None = None,
        context: str | None = None,
        source: str = "unknown",
    ):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return await original_resolve(
            self, name, country_hint=country_hint, context=context, source=source
        )

    registry = InMemorySupplierRegistry(REGISTRY_ENTRIES)
    fresh_resolver = EntityResolver(registry=registry)

    with patch.object(EntityResolver, "resolve", tracking_resolve):
        names = [("Apple Inc", "US")] * 10
        await fresh_resolver.resolve_batch(names, max_concurrent=3)

    assert peak_active <= 3, f"Exceeded max_concurrent: peak was {peak_active}"


# ── InMemorySupplierRegistry ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_in_memory_registry_get_all(registry: InMemorySupplierRegistry) -> None:
    entries = await registry.get_all()
    assert len(entries) == len(REGISTRY_ENTRIES)


@pytest.mark.asyncio
async def test_in_memory_registry_get_by_id(registry: InMemorySupplierRegistry) -> None:
    entry = await registry.get_by_id("sup_apple")
    assert entry is not None
    assert entry.canonical_name == "Apple Inc"


@pytest.mark.asyncio
async def test_in_memory_registry_get_by_id_missing(
    registry: InMemorySupplierRegistry,
) -> None:
    entry = await registry.get_by_id("sup_does_not_exist")
    assert entry is None


@pytest.mark.asyncio
async def test_in_memory_registry_search_by_name(
    registry: InMemorySupplierRegistry,
) -> None:
    results = await registry.search_by_name("apple")
    assert any(e.supplier_id == "sup_apple" for e in results)


@pytest.mark.asyncio
async def test_in_memory_registry_add_unresolved(
    registry: InMemorySupplierRegistry,
) -> None:
    entity = UnresolvedEntity(
        raw_name="Unknown Corp",
        source="news",
        attempted_at=_NOW,
    )
    await registry.add_unresolved(entity)
    assert len(registry.unresolved) == 1
    assert registry.unresolved[0].raw_name == "Unknown Corp"
