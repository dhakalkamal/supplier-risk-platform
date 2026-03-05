# SESSION_3.md — Entity Resolution Pipeline

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md, then read prompts/SESSION_3.md and follow it exactly.
```

Only start after Session 2 checklist is fully green.

---

## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `docs/DATA_SOURCES.md` — "Entity Resolution — The Critical Layer" section

Confirm:
> "Entity resolution is a 3-stage pipeline: exact match → fuzzy match → LLM-assisted. I am building data/pipeline/entity_resolution.py."

---

## RULES FOR THIS SESSION

- This is the most critical piece of infrastructure. A wrong entity match corrupts every downstream signal for that supplier. Quality over speed.
- Every resolution decision must be logged with its confidence score and method.
- Never silently fail — unresolved entities go to a review queue, not /dev/null.
- Run `make test` after Step 3. ≥85% coverage required on `entity_resolution.py`.

---

## STEP 1: Models

Create `data/pipeline/models.py` (shared pipeline models):

```python
class SupplierRegistryEntry(BaseModel):
    supplier_id: str              # internal UUID, prefix "sup_"
    canonical_name: str
    aliases: list[str]            # known alternative names
    country: str                  # ISO 3166-1 alpha-2
    industry_code: str | None
    duns_number: str | None
    website: str | None
    created_at: datetime
    updated_at: datetime

class ResolutionResult(BaseModel):
    raw_name: str                 # input string
    country_hint: str | None
    resolved: bool
    supplier_id: str | None       # None if unresolved
    canonical_name: str | None
    confidence: float             # 0.0–1.0
    method: Literal["exact", "alias", "fuzzy", "llm", "unresolved"]
    matched_string: str | None    # what we actually matched against
    resolved_at: datetime

class UnresolvedEntity(BaseModel):
    raw_name: str
    country_hint: str | None
    source: str                   # "news", "sec", "ais"
    context: str | None           # sentence/snippet where name appeared
    attempted_at: datetime
    attempts: int                 # how many times resolution was tried
```

---

## STEP 2: Entity Resolution Pipeline

Create `data/pipeline/entity_resolution.py`:

```python
class EntityResolver:
    """Three-stage supplier entity resolution pipeline.
    
    Stage 1 — Exact match:
        Normalise both strings (lowercase, strip punctuation, 
        remove legal suffixes like "Inc", "Ltd", "Co.", "Corp").
        Match against canonical_name and all aliases.
        Confidence: 1.0
    
    Stage 2 — Fuzzy match:
        Use rapidfuzz.fuzz.token_sort_ratio.
        Threshold: 85. If country_hint matches, lower threshold to 80.
        Match against canonical_name and all aliases.
        Confidence: ratio / 100
    
    Stage 3 — LLM-assisted (hard cases only):
        Only called when Stage 2 returns candidates between 70–84.
        Sends top 3 candidates to GPT-4o-mini with context.
        Prompt: "Is '{raw_name}' the same company as '{candidate}'? 
                 Context: {context}. Answer JSON: {match: bool, confidence: float}"
        Confidence: from LLM response
        Cost guard: max 100 LLM calls per day (env var LLM_DAILY_LIMIT)
    
    Unresolved:
        If all stages fail, write to unresolved_entities table.
        Do NOT raise — return ResolutionResult(resolved=False).
    """
    
    def __init__(
        self,
        registry: SupplierRegistry,
        llm_client: LLMClient | None = None,  # None = skip Stage 3
    ): ...
    
    async def resolve(
        self,
        raw_name: str,
        country_hint: str | None = None,
        context: str | None = None,
        source: str = "unknown",
    ) -> ResolutionResult: ...
    
    async def resolve_batch(
        self,
        names: list[tuple[str, str | None]],  # (name, country_hint)
        max_concurrent: int = 10,
    ) -> list[ResolutionResult]: ...
    
    def _normalise(self, name: str) -> str: ...
    # Lowercase, strip punctuation, remove legal suffixes
    
    def _stage1_exact(self, normalised: str) -> ResolutionResult | None: ...
    def _stage2_fuzzy(self, normalised: str, country_hint: str | None) -> ResolutionResult | None: ...
    async def _stage3_llm(self, raw_name: str, candidates: list, context: str | None) -> ResolutionResult | None: ...
```

Legal suffixes to strip in normalisation:
```python
LEGAL_SUFFIXES = [
    "inc", "incorporated", "ltd", "limited", "llc", "llp",
    "corp", "corporation", "co", "company", "plc", "ag", "gmbh",
    "sa", "sas", "bv", "nv", "ab", "oy", "as", "holdings",
    "group", "international", "global",
]
```

---

## STEP 3: Supplier Registry Interface

```python
class SupplierRegistry(Protocol):
    """Interface for supplier canonical data. Swappable for testing."""
    async def get_all(self) -> list[SupplierRegistryEntry]: ...
    async def get_by_id(self, supplier_id: str) -> SupplierRegistryEntry | None: ...
    async def search_by_name(self, name: str) -> list[SupplierRegistryEntry]: ...
    async def add_unresolved(self, entity: UnresolvedEntity) -> None: ...

class InMemorySupplierRegistry:
    """Test implementation. Initialise with a list of SupplierRegistryEntry."""
    ...

class PostgresSupplierRegistry:
    """Production implementation backed by Postgres suppliers table."""
    ...
```

---

## STEP 4: CLI Review Tool

Create `data/pipeline/resolve_cli.py` — a command-line tool for manual testing and review:

```bash
# Resolve a single name
python -m data.pipeline.resolve_cli resolve "TSMC" --country TW

# Output:
# Resolved: Taiwan Semiconductor Manufacturing Co (sup_01HX...)
# Method: fuzzy | Confidence: 0.94 | Matched: "taiwan semiconductor manufacturing"

# Batch resolve from CSV
python -m data.pipeline.resolve_cli batch --input suppliers.csv --output results.csv

# Show unresolved queue
python -m data.pipeline.resolve_cli unresolved --limit 20
```

Use `typer` for the CLI. Install it in requirements.txt.

---

## STEP 5: Tests

### `tests/pipeline/test_entity_resolution.py`

Create a fixture with 20 real-ish supplier registry entries for testing.

Test Stage 1 (exact):
- Exact match on canonical name returns confidence 1.0
- Match is case-insensitive
- "Apple Inc" matches "apple" after normalisation
- "Apple Inc." (with period) matches "Apple Inc"
- Alias match works: "TSMC" matches "Taiwan Semiconductor Manufacturing Co"

Test Stage 2 (fuzzy):
- "Taiwan Semiconductor" matches "Taiwan Semiconductor Manufacturing Co" at ≥85
- "Foxcon" (typo) matches "Foxconn" at ≥85
- Completely unrelated names score below threshold
- Country hint lowers threshold: correct country hint + 82 score = match

Test Stage 3 (LLM — mock the LLM client):
- Only called when Stage 2 score is 70–84
- Not called when score < 70 (too different)
- Not called when score ≥ 85 (Stage 2 sufficient)
- LLM returning `{match: false}` → unresolved
- LLM daily limit respected — 101st call returns unresolved without calling API

Test unresolved handling:
- Unresolved entity written to registry (via mock)
- `resolved=False` returned, no exception raised

Test batch resolution:
- 10 names resolved concurrently (mock confirms concurrent calls)
- One failure doesn't block others

Test normalisation:
- "Apple Inc." → "apple"
- "台積電 Co., Ltd." → "台積電" (preserve non-ASCII)
- "SAMSUNG ELECTRONICS CO., LTD" → "samsung electronics"

**Run `make test` — ≥85% coverage required on `entity_resolution.py`.**

---

## SESSION 3 DONE — CHECKLIST

```
□ make lint passes clean
□ make test passes — ≥85% coverage on entity_resolution.py
□ Stage 1: exact match after normalisation, confidence 1.0
□ Stage 2: rapidfuzz token_sort_ratio, threshold 85 (80 with country hint)
□ Stage 3: only triggered for scores 70–84, LLM mocked in tests
□ LLM daily call limit enforced (env var)
□ Unresolved entities written to registry, never silently dropped
□ resolve_batch uses asyncio concurrency (not sequential)
□ CLI tool works: resolve single name, batch CSV, show unresolved queue
□ InMemorySupplierRegistry and PostgresSupplierRegistry both implemented
□ Every resolution logged with method + confidence score
```

**Say: "Session 3 complete. Checklist: X/11 items green."**

Next: `prompts/SESSION_4.md`
