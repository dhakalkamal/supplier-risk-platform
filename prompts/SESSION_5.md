# SESSION_5.md — Heuristic Risk Scorer (MVP v0 Model)

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md, then read prompts/SESSION_5.md and follow it exactly.
```

Only start after Session 4 checklist is fully green.

---

## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `specs/ML_SPEC.md` — "Phase 1 ML Tasks" section and "Explainability (SHAP)" section
3. `docs/ARCHITECTURE.md` — "Snowflake Schema / SCORES schema" section

Confirm:
> "I am building the v0 heuristic scorer. It replaces the full XGBoost model until we have training data. It must produce a 0–100 score with SHAP-equivalent signal attribution. Output goes to scores.supplier_daily_scores."

---

## RULES FOR THIS SESSION

- The heuristic scorer must produce the EXACT same output schema as the future ML model. The ML model in Phase 2 is a drop-in replacement — same inputs, same output format.
- Signal attribution must be human-readable. "Financial stress is +20 because Altman Z-Score is 1.1 (distress zone)" — not just "+20".
- Every score written to the database must have a model_version tag ("heuristic_v0").
- Run `make test` after Step 3. ≥85% coverage required.

---

## STEP 1: Score Output Models

Create `ml/scoring/models.py`:

```python
class SignalContribution(BaseModel):
    """Attribution for a single signal's contribution to the risk score."""
    signal_name: str              # e.g. "altman_z_score"
    display_name: str             # e.g. "Financial Stress Index"
    category: Literal["financial", "news", "shipping", "geopolitical", "macro"]
    raw_value: float | None       # the actual feature value
    contribution: float           # points added to score (-100 to +100)
    direction: Literal["increases_risk", "decreases_risk", "neutral"]
    explanation: str              # human-readable: "Altman Z-Score of 1.1 is in distress zone (<1.23)"

class RiskScoreOutput(BaseModel):
    """Complete risk score output — same schema for heuristic and ML model."""
    supplier_id: str
    score: int                    # 0–100, higher = more risk
    risk_level: Literal["low", "medium", "high"]
    # Signal breakdown by category (weighted averages)
    financial_score: float        # 0–100 for this category
    news_score: float
    shipping_score: float
    geo_score: float
    macro_score: float
    # Top drivers (for UI display — sorted by abs(contribution) desc)
    top_drivers: list[SignalContribution]   # top 5
    all_signals: list[SignalContribution]   # all signals
    # Metadata
    model_version: str            # "heuristic_v0" or "xgboost_v1" later
    feature_date: date            # date of features used
    scored_at: datetime
    data_completeness: float      # 0.0–1.0, fraction of signals available

class DailyScoreRecord(BaseModel):
    """Row written to scores.supplier_daily_scores."""
    id: str                       # UUID
    supplier_id: str
    score: int
    risk_level: str
    signal_breakdown: dict        # full RiskScoreOutput as JSON
    model_version: str
    feature_date: date
    scored_at: datetime
```

---

## STEP 2: Heuristic Scorer

Create `ml/scoring/heuristic_scorer.py`:

```python
class HeuristicRiskScorer:
    """Rules-based supplier risk scorer (v0, pre-ML).
    
    Scores suppliers 0–100 based on weighted signal rules derived from
    financial stress literature (Altman, 1968) and supply chain risk research.
    
    This is a temporary model used until we have enough labelled disruption
    events to train XGBoost (target: 100+ events, ~3 months of data).
    
    The output schema is identical to the ML model — it's a drop-in replacement.
    """
    
    MODEL_VERSION = "heuristic_v0"
    
    # Category weights — must sum to 1.0
    WEIGHTS = {
        "financial":    0.30,
        "news":         0.25,
        "shipping":     0.20,
        "geopolitical": 0.15,
        "macro":        0.10,
    }
    
    def score(self, features: SupplierFeatureVector) -> RiskScoreOutput: ...
    
    def _score_financial(self, features: SupplierFeatureVector) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for financial category.
        
        Rules:
        - Altman Z-Score < 1.23 (distress):    +30 points, explanation: "In distress zone"
        - Altman Z-Score 1.23–2.90 (grey):     +15 points, explanation: "In grey zone"  
        - Altman Z-Score > 2.90 (safe):         +0 points
        - Altman Z-Score is None (no data):     +10 points (uncertainty penalty)
        - going_concern_flag = True:            +25 points
        - current_ratio < 1.0:                 +10 points
        - debt_to_equity > 2.0:                +10 points
        - interest_coverage < 1.5:             +10 points
        - financial_data_is_stale = True:      +10 points (>180 days old)
        
        Cap at 100.
        """
        ...
    
    def _score_news(self, features: SupplierFeatureVector) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for news category.
        
        Rules:
        - negative_article_count_30d >= 5:     +25 points
        - negative_article_count_30d 2–4:      +15 points
        - sentiment_score_30d < -0.5:          +20 points
        - negative_velocity > 2.0:             +15 points (accelerating)
        - topic_bankruptcy_flag_30d = True:    +30 points
        - topic_layoff_flag_30d = True:        +15 points
        - topic_strike_flag_30d = True:        +15 points
        - topic_disaster_flag_30d = True:      +20 points
        - topic_regulatory_flag_30d = True:    +15 points
        
        Cap at 100.
        """
        ...
    
    def _score_shipping(self, features: SupplierFeatureVector) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for shipping category.
        
        Rules:
        - shipping_volume_delta_30d < -0.5:    +30 points (>50% drop)
        - shipping_volume_delta_30d < -0.3:    +20 points (>30% drop)
        - shipping_volume_z_score < -2:        +25 points (statistical anomaly)
        - shipping_anomaly_flag = True:        +20 points
        - dwell_time_delta > 48 (hours):       +15 points (ships waiting longer)
        - No shipping data available:          +10 points (uncertainty)
        
        Cap at 100.
        """
        ...
    
    def _score_geo(self, features: SupplierFeatureVector) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for geopolitical category.
        
        Rules:
        - on_sanctions_list = True:            +50 points
        - country_under_sanctions = True:      +30 points
        - country_risk_score > 75:             +25 points
        - country_risk_score 50–75:            +15 points
        - country_risk_trend_90d > 10:         +10 points (worsening trend)
        
        Cap at 100.
        """
        ...
    
    def _score_macro(self, features: SupplierFeatureVector) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for macro category.
        
        Rules:
        - industry_pmi < 45:                   +25 points (contraction)
        - industry_pmi 45–50:                  +10 points (near contraction)
        - commodity_price_delta_30d > 0.20:    +15 points (>20% input cost rise)
        - high_yield_spread_delta_30d > 0.5:   +15 points (credit stress rising)
        
        Cap at 100.
        """
        ...
    
    def _compute_final_score(
        self,
        category_scores: dict[str, float],
    ) -> int:
        """Weighted average of category scores → 0–100 integer."""
        raw = sum(
            score * self.WEIGHTS[category]
            for category, score in category_scores.items()
        )
        return int(round(raw))
    
    def _risk_level(self, score: int) -> str:
        if score >= 70: return "high"
        if score >= 40: return "medium"
        return "low"
    
    def _data_completeness(self, features: SupplierFeatureVector) -> float:
        """Fraction of expected signals that are non-None."""
        ...
```

---

## STEP 3: Score Storage

Create `ml/scoring/score_repository.py`:

```python
class ScoreRepository(Protocol):
    async def upsert_daily_score(self, record: DailyScoreRecord) -> None: ...
    async def get_latest_score(self, supplier_id: str) -> DailyScoreRecord | None: ...
    async def get_score_history(
        self, supplier_id: str, days: int = 90
    ) -> list[DailyScoreRecord]: ...

class InMemoryScoreRepository:
    """For tests. Stores scores in a dict keyed by (supplier_id, feature_date)."""
    ...

class SnowflakeScoreRepository:
    """Writes to scores.supplier_daily_scores in Snowflake."""
    ...
```

---

## STEP 4: Scoring Runner

Create `ml/scoring/run_scoring.py` — the script that Airflow will call:

```python
"""
Scoring runner. Called by Airflow DAG ml_score_suppliers.

1. Reads supplier_feature_vector from Snowflake (all suppliers, today's features)
2. Scores each supplier using HeuristicRiskScorer
3. Writes DailyScoreRecord to scores.supplier_daily_scores
4. Publishes scores.updated event to Kafka (for alert engine)
5. Logs summary: total scored, high/medium/low breakdown, any failures
"""

async def run_daily_scoring(feature_date: date | None = None) -> ScoringRunSummary: ...

# Can be run manually: python -m ml.scoring.run_scoring --date 2025-03-04
```

Add to Makefile:
```makefile
make score   # python -m ml.scoring.run_scoring
```

---

## STEP 5: Airflow DAG

Create `data/dags/ml_score_suppliers.py`:
- Schedule: every 6 hours (`"0 */6 * * *"`)
- Single task: call `run_daily_scoring()`
- Retry: 2 attempts, 10-minute delay
- Log summary metrics as structured logs

---

## STEP 6: Tests

### `tests/ml/test_heuristic_scorer.py`

```python
# Build a complete test fixture: SupplierFeatureVector with known values
# Verify scores manually against the rules

def test_high_risk_supplier():
    features = SupplierFeatureVector(
        supplier_id="sup_test_001",
        altman_z_score=0.8,          # distress zone → +30
        going_concern_flag=True,      # → +25
        negative_article_count_30d=6, # → +25
        on_sanctions_list=False,
        country_risk_score=80,        # → +25
        shipping_volume_delta_30d=-0.6, # → +30
        # ... fill remaining fields
    )
    result = scorer.score(features)
    assert result.score >= 70        # should be high risk
    assert result.risk_level == "high"
    assert len(result.top_drivers) == 5

def test_low_risk_supplier():
    # All signals green — should score < 40
    ...

def test_missing_financial_data():
    # altman_z_score = None → uncertainty penalty applied, not zero
    features = SupplierFeatureVector(altman_z_score=None, ...)
    result = scorer.score(features)
    # financial contribution should be 10 (uncertainty), not 0
    financial_driver = next(d for d in result.all_signals if d.signal_name == "altman_z_score")
    assert financial_driver.contribution == 10

def test_sanctions_hit_dominates():
    # on_sanctions_list = True → geo score → 50+ → should push total score high
    ...

def test_score_bounded():
    # Pathological inputs — score must stay 0–100
    ...

def test_data_completeness():
    # All fields None → completeness = 0.0
    # All fields populated → completeness = 1.0
    ...

def test_output_schema_matches_ml_spec():
    # RiskScoreOutput has all required fields
    # top_drivers has exactly 5 entries
    # all_signals sorted by abs(contribution) descending
    ...

def test_model_version_tag():
    result = scorer.score(any_features)
    assert result.model_version == "heuristic_v0"
```

**Run `make test` — ≥85% coverage required on `ml/scoring/`.**

---

## FINAL PHASE 1 CHECKLIST

This is the end of Phase 1. Before moving to Phase 2 (ML model training), verify ALL of these:

```
□ make lint passes clean — zero ruff and mypy errors
□ make test passes — ≥80% overall, ≥85% on entity_resolution and ml/scoring
□ make dev — docker-compose starts all 5 services cleanly with health checks
□ make ingest-sec — triggers SEC scraper, at least one record in raw.sec Kafka topic
□ SEC EDGAR pipeline: rate-limited, retries, DLQ working
□ News pipeline: NewsAPI + GDELT, FinBERT with lexicon fallback, DLQ working
□ Entity resolution: 3 stages, LLM daily limit, unresolved queue working
□ dbt compile — all models compile with zero errors
□ dbt test — all schema tests pass (run against fixture data if no Snowflake yet)
□ Heuristic scorer: produces 0–100 score with signal attribution
□ Heuristic scorer output schema identical to ML_SPEC.md RiskScoreOutput definition
□ All scores tagged model_version="heuristic_v0"
□ Airflow DAGs: ingest_sec, ingest_news, ml_score_suppliers all defined and valid
□ No Snowflake live calls in any test — all behind repository interfaces
□ No print() anywhere — structlog only
□ No raw dicts between functions — Pydantic models everywhere
□ .env.example complete — every env var documented
```

**Say: "Phase 1 complete. Final checklist: X/17 items green."**

If anything is red — fix it before starting Phase 2.
Phase 2 prompt will be added to `prompts/` when Phase 1 is confirmed done.
