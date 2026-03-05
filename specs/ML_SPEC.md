# ML_SPEC.md — Machine Learning Specification

> Read this before writing ANY ML code, feature engineering, model training, or scoring pipeline.
> If something is not specified here, check DECISIONS.md before inventing an answer.
> Every design choice here has a reason — understand it before changing it.

---

## 1. Problem Framing

**Task:** Given a supplier's observable public signals over the past 90 days, predict the probability that this supplier will experience a significant operational disruption within the next 30/60/90 days.

**Why survival analysis framing, not binary classification:**

Binary classification answers "is this supplier risky?" — a point-in-time judgement with no time horizon.
Survival analysis answers "what is the probability of failure within N days?" — directly actionable for procurement decisions.

This is identical to the framing used in credit risk (time-to-default modelling). A procurement manager asking "should I dual-source this supplier?" needs a time horizon to act on, not just a flag.

We approximate this with XGBoost probability scores rather than a full survival model. A full Cox Proportional Hazards survival model is a future consideration once we have 500+ labelled disruption events.

**What counts as a disruption event (label = 1):**
- Supplier fails to fulfill a confirmed purchase order (customer-reported)
- Bankruptcy filing (Chapter 11, Chapter 7, administration, liquidation)
- Public announcement of factory closure or major production halt
- Supply disruption notice issued to customers
- Proxy: 80%+ drop in shipping volume sustained over 30 days

**Output:** A calibrated risk score 0–100 per supplier, updated every 6 hours, with SHAP attribution on every score. Higher = more risk.

---

## 2. The `SupplierFeatureVector` — Single Source of Truth

This Pydantic model is the contract between the dbt feature store and the ML pipeline.
The dbt model `pipeline.supplier_feature_vector` must produce columns matching these field names exactly.
The ML pipeline reads this model. If a column name changes in dbt, update it here first.

```python
# ml/features/feature_vector.py

from pydantic import BaseModel, field_validator
from datetime import date, datetime

class SupplierFeatureVector(BaseModel):
    """Complete feature vector for one supplier on one date.
    
    None means the signal is genuinely missing — not zero, not unknown.
    The model must handle None explicitly (see Section 4: Missing Data).
    Never substitute 0 for None — it biases every ratio that uses this feature.
    """
    
    # ── Identity ──────────────────────────────────────────────────────────────
    supplier_id:                        str
    feature_date:                       date
    
    # ── Financial Features (30% weight) ───────────────────────────────────────
    # From: staging.stg_sec_financials → marts.supplier_financial_features
    altman_z_score:                     float | None  # Z' score (private company formula)
    altman_working_capital_ratio:       float | None  # working_capital / total_assets
    altman_retained_earnings_ratio:     float | None  # retained_earnings / total_assets
    altman_ebit_ratio:                  float | None  # ebit / total_assets
    altman_equity_to_debt:              float | None  # book_equity / total_liabilities
    altman_revenue_ratio:               float | None  # revenue / total_assets
    going_concern_flag:                 bool | None   # True if 10-K flags going concern
    current_ratio:                      float | None  # current_assets / current_liabilities
    quick_ratio:                        float | None  # (current_assets - inventory) / current_liabilities
    cash_ratio:                         float | None  # cash / current_liabilities
    debt_to_equity:                     float | None  # total_debt / shareholders_equity
    interest_coverage:                  float | None  # ebit / interest_expense
    revenue_growth_qoq:                 float | None  # (revenue_q - revenue_q1) / revenue_q1
    gross_margin_trend:                 float | None  # gross_margin_q - gross_margin_q4
    financial_data_staleness_days:      int | None    # days since last filing
    financial_data_is_stale:            bool          # True if staleness_days > 180
    is_public_company:                  bool          # False = private, financial features likely None
    
    # ── News Sentiment Features (25% weight) ──────────────────────────────────
    # From: staging.stg_news_sentiment → marts.supplier_news_features
    news_sentiment_7d:                  float | None  # mean sentiment last 7d (-1 to +1)
    news_sentiment_30d:                 float | None  # mean sentiment last 30d
    news_negative_count_30d:            int | None    # articles with sentiment < -0.3
    news_negative_velocity:             float | None  # negative_7d / negative_30d ratio
    news_credibility_weighted_score:    float | None  # sentiment weighted by source credibility
    topic_layoff_30d:                   bool          # layoff-related news in last 30d
    topic_bankruptcy_30d:               bool          # bankruptcy-related news in last 30d
    topic_strike_30d:                   bool          # strike/industrial action in last 30d
    topic_disaster_30d:                 bool          # fire/explosion/disaster in last 30d
    topic_regulatory_30d:               bool          # regulatory action/fine in last 30d
    news_article_count_30d:             int | None    # total articles (coverage check)
    
    # ── Shipping Volume Features (20% weight) ─────────────────────────────────
    # From: staging.stg_shipping_volume → marts.supplier_shipping_features
    port_call_count_30d:                int | None    # vessel calls at primary port, 30d
    port_call_count_90d:                int | None    # vessel calls at primary port, 90d
    shipping_volume_delta_30d:          float | None  # % change vs prior 30d
    shipping_volume_z_score:            float | None  # z-score vs historical baseline
    avg_port_dwell_time_7d:             float | None  # mean hours at berth, 7d
    dwell_time_delta:                   float | None  # avg_dwell_7d - avg_dwell_90d
    shipping_anomaly_flag:              bool          # True if z_score < -2 or 50%+ drop
    port_mapping_confidence:            float | None  # 0.0-1.0, how confident is port mapping
    
    # ── Geopolitical Risk Features (15% weight) ───────────────────────────────
    # From: staging.stg_geo_risk → marts.supplier_geo_features
    country_risk_score:                 float | None  # composite 0-100, higher = more risk
    country_risk_trend_90d:             float | None  # delta vs 90 days ago
    on_sanctions_list:                  bool          # direct OFAC SDN hit
    parent_on_sanctions_list:           bool          # parent/subsidiary on list
    country_under_sanctions:            bool          # broad country-level sanctions
    single_country_exposure:            bool          # all primary ops in one country
    
    # ── Macro / Input Cost Features (10% weight) ──────────────────────────────
    # From: staging.stg_macro_indicators → marts.supplier_macro_features
    commodity_price_delta_30d:          float | None  # % change in primary input commodity
    energy_price_index_30d:             float | None  # regional energy cost delta
    high_yield_spread_delta_30d:        float | None  # HY spread change (credit stress proxy)
    industry_pmi:                       float | None  # PMI for supplier's industry
    
    # ── Data Quality Metadata ─────────────────────────────────────────────────
    data_completeness:                  float         # 0.0-1.0, fraction of non-None signals
    feature_vector_created_at:          datetime

    @field_validator("altman_z_score")
    @classmethod
    def z_score_reasonable(cls, v: float | None) -> float | None:
        """Z-scores outside -10 to 20 indicate data error, not genuine distress."""
        if v is not None and not (-10 <= v <= 20):
            raise ValueError(f"Altman Z-Score {v} is outside plausible range [-10, 20]")
        return v

    @field_validator("data_completeness")
    @classmethod
    def completeness_bounded(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"data_completeness must be 0.0-1.0, got {v}")
        return v
```

---

## 3. Feature Engineering — Safe Computation Rules

### 3.1 Altman Z-Score (Private Company Formula)

We use the **Z' model (Altman 1983)** for private companies, not the original 1968 model.
The original uses market capitalisation (not available for private companies).
Z' uses book value of equity instead.

```python
# ml/features/financial.py

def compute_altman_z_score_private(
    working_capital: float | None,
    total_assets: float | None,
    retained_earnings: float | None,
    ebit: float | None,
    book_equity: float | None,
    total_liabilities: float | None,
    revenue: float | None,
) -> float | None:
    """Compute Altman Z' score for private companies.
    
    Formula: Z' = 0.717*X1 + 0.847*X2 + 3.107*X3 + 0.420*X4 + 0.998*X5
    
    Interpretation:
        Z' < 1.23  → Distress zone (high bankruptcy risk)
        1.23–2.90  → Grey zone (ambiguous)
        Z' > 2.90  → Safe zone
    
    Returns None if ANY required input is None or if total_assets == 0.
    Never returns a Z-score computed from partial data — partial = None.
    """
    inputs = [working_capital, total_assets, retained_earnings,
              ebit, book_equity, total_liabilities, revenue]
    
    if any(v is None for v in inputs):
        return None
    
    if total_assets == 0 or total_liabilities == 0:
        return None  # division guard — not a zero, genuinely undefined
    
    x1 = working_capital / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x4 = book_equity / total_liabilities
    x5 = revenue / total_assets
    
    return 0.717*x1 + 0.847*x2 + 3.107*x3 + 0.420*x4 + 0.998*x5


# Thresholds — use these constants everywhere, never hardcode
Z_SCORE_DISTRESS_THRESHOLD = 1.23   # Z' < 1.23 = distress (private company)
Z_SCORE_GREY_THRESHOLD = 2.90       # Z' < 2.90 = grey zone
```

### 3.2 Safe Ratio Computation

Every ratio has a denominator that could be zero or None. Never divide without guarding.

```python
# ml/features/utils.py

def safe_ratio(
    numerator: float | None,
    denominator: float | None,
    floor: float | None = None,
    cap: float | None = None,
) -> float | None:
    """Safely compute numerator/denominator.
    
    Returns None if either input is None or denominator is zero.
    Optionally clamps result to [floor, cap] to handle outliers.
    
    Never returns 0 to represent a missing value.
    """
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    result = numerator / denominator
    if floor is not None:
        result = max(floor, result)
    if cap is not None:
        result = min(cap, result)
    return result


# Usage:
current_ratio = safe_ratio(current_assets, current_liabilities, floor=0.0, cap=10.0)
quick_ratio   = safe_ratio(current_assets_minus_inventory, current_liabilities, floor=0.0)
cash_ratio    = safe_ratio(cash, current_liabilities, floor=0.0)
debt_to_equity = safe_ratio(total_debt, shareholders_equity, floor=0.0, cap=50.0)
interest_coverage = safe_ratio(ebit, interest_expense, floor=-20.0, cap=50.0)
```

### 3.3 Shipping Volume Delta

```python
def compute_shipping_delta(
    calls_30d: int | None,
    calls_prior_30d: int | None,
) -> float | None:
    """Compute % change in port calls vs prior period.
    
    Returns None if either period has no data.
    Returns -1.0 if prior period had calls but current has zero (100% drop).
    Never returns a delta when baseline is zero (undefined % change).
    """
    if calls_30d is None or calls_prior_30d is None:
        return None
    if calls_prior_30d == 0:
        return None  # can't compute % change from zero baseline
    if calls_30d == 0:
        return -1.0  # 100% drop — special case, valid signal
    return (calls_30d - calls_prior_30d) / calls_prior_30d


def compute_shipping_z_score(
    calls_30d: int | None,
    historical_mean: float | None,
    historical_std: float | None,
) -> float | None:
    """Z-score of current 30d volume vs historical baseline."""
    if any(v is None for v in [calls_30d, historical_mean, historical_std]):
        return None
    if historical_std == 0:
        return None
    return (calls_30d - historical_mean) / historical_std
```

### 3.4 News Velocity

```python
def compute_news_negative_velocity(
    negative_count_7d: int | None,
    negative_count_30d: int | None,
) -> float | None:
    """Ratio of recent negative news to monthly baseline.
    
    Values > 1.0 mean negative news is accelerating.
    Returns None if 30d count is zero or None (no baseline to compare against).
    Capped at 4.0 to prevent single-day spikes from dominating.
    """
    if negative_count_7d is None or negative_count_30d is None:
        return None
    if negative_count_30d == 0:
        return None
    velocity = (negative_count_7d / 7) / (negative_count_30d / 30)
    return min(velocity, 4.0)  # cap at 4x to prevent outlier dominance
```

---

## 4. Missing Data Strategy

Missing data is a first-class concern — not an afterthought. Handle it explicitly everywhere.

### 4.1 Missing Data Philosophy

```
None  = signal is genuinely absent (no filing, no news, no port data)
0.0   = signal is present and its value is zero (zero debt, zero articles)

These are DIFFERENT. Treating None as 0 biases every model that uses that feature.
A supplier with no SEC data is different from a supplier with $0 assets.
```

### 4.2 XGBoost Missing Data Handling

XGBoost has native support for missing values — it learns the optimal direction for
missing values during training. Use this — do not impute before XGBoost.

```python
import numpy as np

def feature_vector_to_xgb_array(fv: SupplierFeatureVector) -> np.ndarray:
    """Convert feature vector to numpy array for XGBoost.
    
    None → np.nan (XGBoost handles natively — learns optimal split direction)
    bool → float (True=1.0, False=0.0, None=np.nan)
    
    CRITICAL: Column order must match training exactly.
    Use FEATURE_COLUMNS list below — never rely on dict ordering.
    """
    values = []
    for col in FEATURE_COLUMNS:
        val = getattr(fv, col)
        if val is None:
            values.append(np.nan)
        elif isinstance(val, bool):
            values.append(1.0 if val else 0.0)
        else:
            values.append(float(val))
    return np.array(values, dtype=np.float32)


# FEATURE_COLUMNS — canonical ordered list of features fed to XGBoost
# This list defines the model's feature space. Never reorder without retraining.
FEATURE_COLUMNS = [
    # Financial (30%)
    "altman_z_score",
    "altman_working_capital_ratio",
    "altman_retained_earnings_ratio",
    "altman_ebit_ratio",
    "altman_equity_to_debt",
    "altman_revenue_ratio",
    "going_concern_flag",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "debt_to_equity",
    "interest_coverage",
    "revenue_growth_qoq",
    "gross_margin_trend",
    "financial_data_is_stale",
    "is_public_company",
    # News (25%)
    "news_sentiment_7d",
    "news_sentiment_30d",
    "news_negative_count_30d",
    "news_negative_velocity",
    "news_credibility_weighted_score",
    "topic_layoff_30d",
    "topic_bankruptcy_30d",
    "topic_strike_30d",
    "topic_disaster_30d",
    "topic_regulatory_30d",
    "news_article_count_30d",
    # Shipping (20%)
    "port_call_count_30d",
    "port_call_count_90d",
    "shipping_volume_delta_30d",
    "shipping_volume_z_score",
    "avg_port_dwell_time_7d",
    "dwell_time_delta",
    "shipping_anomaly_flag",
    "port_mapping_confidence",
    # Geopolitical (15%)
    "country_risk_score",
    "country_risk_trend_90d",
    "on_sanctions_list",
    "parent_on_sanctions_list",
    "country_under_sanctions",
    "single_country_exposure",
    # Macro (10%)
    "commodity_price_delta_30d",
    "energy_price_index_30d",
    "high_yield_spread_delta_30d",
    "industry_pmi",
]
```

### 4.3 Cold-Start Strategy (New Suppliers)

A new supplier added today has zero historical data. It cannot have a meaningful score
from day one. Here is the explicit cold-start policy:

```
Days 0–7:   Return score = None, risk_level = "insufficient_data"
            Show "Monitoring — gathering data" in the UI
            Do not show in high-risk lists
            
Days 8–30:  Score using only available signals (data_completeness < 0.5)
            Append warning: "Score based on partial data"
            Use heuristic scorer regardless of whether ML model is deployed
            
Days 31+:   Full scoring. If key signals still missing, reflect in data_completeness

Rationale: A score of 50 (our heuristic baseline) for a new supplier implies
"medium risk" — which could be wrong and misleading. Unknown ≠ medium.
```

---

## 5. Data Leakage Prevention

Time-series ML has many leakage vectors. Violating these rules produces models that look
great in evaluation but fail in production.

### 5.1 Temporal Split — Non-Negotiable

```python
# ml/training/splits.py

def create_temporal_splits(
    df: pd.DataFrame,
    date_col: str = "feature_date",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create train/validation/test splits with strict temporal ordering.
    
    Split:
        Test:       Last 6 months  (held out — never touched until final evaluation)
        Validation: Months 7-9     (hyperparameter tuning, early stopping)
        Train:      Everything before validation
    
    NEVER use random splits on time-series data — it leaks future information.
    NEVER shuffle before splitting.
    """
    df = df.sort_values(date_col)
    dates = pd.to_datetime(df[date_col])
    
    test_start  = dates.max() - pd.DateOffset(months=6)
    val_start   = dates.max() - pd.DateOffset(months=9)
    
    test  = df[dates >= test_start]
    val   = df[(dates >= val_start) & (dates < test_start)]
    train = df[dates < val_start]
    
    assert len(train) > 0, "Training set is empty"
    assert len(val) > 0,   "Validation set is empty"
    assert len(test) > 0,  "Test set is empty"
    
    # Verify no temporal leakage
    assert train[date_col].max() < val[date_col].min(), "Train/val overlap"
    assert val[date_col].max() < test[date_col].min(),  "Val/test overlap"
    
    return train, val, test
```

### 5.2 Leakage Checklist — Run Before Every Training Run

```
□ Temporal split applied before ANY preprocessing
□ Scalers/imputers fitted on train only, applied to val/test
□ No future-looking features (e.g. "did supplier fail in next 30 days" leaking into features)
□ Rolling window features computed using only past data (no look-ahead)
□ SMOTE applied AFTER train/val/test split, on train only (see Section 5.3)
□ Feature importance checked — any feature with importance > 0.3 is suspicious (likely leakage)
□ Validation AUC >> Train AUC is impossible — if seen, there is leakage
□ Test set not touched until final model selection
```

### 5.3 SMOTE — Safe Application

SMOTE oversamples minority class to address label imbalance (~2-5% positive rate).
Applied incorrectly it causes severe data leakage.

```python
from imblearn.over_sampling import SMOTE

# ✅ Correct — SMOTE on training data only, AFTER temporal split
smote = SMOTE(
    k_neighbors=5,
    random_state=42,
    sampling_strategy=0.2,   # target 20% positive rate (not 50% — too aggressive)
)
X_train_resampled, y_train_resampled = smote.fit_resample(X_train, y_train)

# ❌ NEVER apply SMOTE before splitting
# ❌ NEVER apply SMOTE to validation or test sets
# ❌ NEVER use SMOTE with k_neighbors > number of positive examples in training set
```

---

## 6. Model Architecture

### 6.1 Heuristic Scorer v0 (Phase 1 — Ships First)

Rules-based scorer. Ships before we have training data.
Must produce identical `RiskScoreOutput` schema as XGBoost v1 (see ADR-014).

```python
# ml/scoring/heuristic_scorer.py
# Full implementation spec in SESSION_5.md

MODEL_VERSION = "heuristic_v0"

# Baseline score: 30 (not 50)
# Rationale: Most suppliers are not at risk. Starting at 50 implies
# "medium risk" for every new supplier — misleading and creates alert fatigue.
# 30 = "probably fine, watching" — a more honest prior.
BASELINE_SCORE = 30

# Rules applied on top of baseline (see SESSION_5.md for full table)
# Maximum additive contribution from each category:
#   Financial:    +40 points max
#   News:         +35 points max
#   Shipping:     +30 points max
#   Geopolitical: +35 points max (sanctions = immediate +50, can exceed cap)
#   Macro:        +20 points max
```

**Note on baseline change from earlier drafts:** Changed from 50 to 30.
50 implies medium risk for all new suppliers. 30 is a more honest prior —
the vast majority of suppliers are not in distress at any given time.

### 6.2 XGBoost Classifier v1 (Phase 2)

```python
# ml/training/xgb_trainer.py

import xgboost as xgb
import mlflow

XGB_PARAMS = {
    "n_estimators": 500,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,      # prevents overfitting on sparse positive examples
    "scale_pos_weight": 20,     # handles ~5% positive rate (negative/positive ratio)
    "eval_metric": ["auc", "aucpr"],
    "early_stopping_rounds": 50,
    "random_state": 42,
    "tree_method": "hist",      # faster training, same accuracy as "exact"
    "enable_categorical": False, # we encode booleans as float — no categoricals
}

def train_xgb_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
    experiment_name: str = "supplier_risk_xgb",
) -> tuple[xgb.XGBClassifier, dict]:
    """Train XGBoost with MLflow tracking.
    
    Every training run is logged. No exceptions.
    Returns trained model and metrics dict.
    """
    with mlflow.start_run():
        mlflow.log_params(XGB_PARAMS)
        mlflow.log_param("n_train", len(X_train))
        mlflow.log_param("n_val", len(X_val))
        mlflow.log_param("positive_rate_train", y_train.mean())
        mlflow.log_param("feature_count", len(feature_names))
        
        model = xgb.XGBClassifier(**XGB_PARAMS)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=50,
        )
        
        # Evaluate
        metrics = evaluate_model(model, X_val, y_val, X_train, y_train)
        mlflow.log_metrics(metrics)
        
        # Feature importance plot
        importance_fig = plot_feature_importance(model, feature_names)
        mlflow.log_figure(importance_fig, "feature_importance.png")
        
        # SHAP summary plot
        shap_fig = plot_shap_summary(model, X_val, feature_names)
        mlflow.log_figure(shap_fig, "shap_summary.png")
        
        # Log model
        mlflow.xgboost.log_model(model, "model")
        
        return model, metrics
```

### 6.3 Anomaly Detection: Isolation Forest

Used as an auxiliary signal in the ensemble. Detects suppliers with unusual
feature combinations that don't match known disruption patterns.

```python
# ml/training/anomaly_trainer.py

from sklearn.ensemble import IsolationForest

def train_isolation_forest(
    X_train: np.ndarray,
    contamination: float = 0.05,  # expected anomaly rate ~= positive event rate
) -> IsolationForest:
    """Train Isolation Forest for anomaly detection.
    
    Trained on ALL suppliers (including positives) — it detects unusual patterns,
    not just positive patterns.
    
    contamination: expected fraction of outliers in the dataset.
    Set to approx the positive event rate.
    
    Output: anomaly_score in [0, 1] where higher = more anomalous.
    """
    iso = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    iso.fit(X_train)
    return iso


def get_anomaly_score(
    iso: IsolationForest,
    features: np.ndarray,
) -> float:
    """Return anomaly score in [0, 1]. Higher = more anomalous."""
    # IsolationForest decision_function returns negative = anomaly
    # Rescale to [0, 1] where 1 = most anomalous
    raw = iso.decision_function(features.reshape(1, -1))[0]
    # Raw score range is approximately [-0.5, 0.5]
    # Normalise: 0.5 → 0.0 (normal), -0.5 → 1.0 (anomalous)
    return float(max(0.0, min(1.0, (0.5 - raw))))
```

### 6.4 Ensemble Score Computation

```python
# ml/scoring/ensemble.py

def compute_ensemble_risk_score(
    xgb_proba: float,            # XGBoost P(disruption in 90d), range [0, 1]
    news_sentiment_score: float,  # range [-1, +1], more negative = more risk
    anomaly_score: float,         # Isolation Forest score, range [0, 1]
    data_completeness: float,     # range [0, 1]
) -> int:
    """Combine model outputs into a single 0-100 risk score.

    Weights: XGBoost 50%, News 30%, Anomaly 20%

    News sentiment inverted: -1 (max negative) → 1.0 (max risk contribution)
                             +1 (max positive) → 0.0 (min risk contribution)

    Completeness penalty: if data_completeness < 0.4, add uncertainty penalty.
    A score based on < 40% of signals is unreliable — flag it.
    """
    news_risk = (1.0 - (news_sentiment_score + 1.0) / 2.0)  # invert to risk scale

    raw = (
        0.50 * xgb_proba +
        0.30 * news_risk +
        0.20 * anomaly_score
    )
    
    score = int(round(raw * 100))
    score = max(0, min(100, score))  # clamp to [0, 100]
    
    # Completeness penalty: push low-completeness scores toward 50 (uncertain)
    if data_completeness < 0.4:
        score = int(score * data_completeness + 50 * (1 - data_completeness))
        score = max(0, min(100, score))
    
    return score


def risk_level_from_score(score: int) -> str:
    if score >= 70: return "high"
    if score >= 40: return "medium"
    return "low"
```

---

## 7. Probability Calibration

Raw XGBoost probabilities are not well-calibrated — a raw output of 0.70 does not mean
70% probability of disruption. Calibration is required before using outputs as scores.

```python
# ml/training/calibration.py

from sklearn.calibration import CalibratedClassifierCV, calibration_curve
import matplotlib.pyplot as plt

def calibrate_model(
    model: xgb.XGBClassifier,
    X_val: np.ndarray,
    y_val: np.ndarray,
    method: str = "isotonic",  # "isotonic" for >1000 samples, "sigmoid" (Platt) for <1000
) -> CalibratedClassifierCV:
    """Apply probability calibration to XGBoost model.
    
    Isotonic regression preferred (more flexible, requires >1000 samples).
    Sigmoid (Platt scaling) for smaller validation sets.
    
    The calibrated model wraps the original — use calibrated_model.predict_proba()
    for all production scoring.
    """
    calibrated = CalibratedClassifierCV(
        model,
        method=method,
        cv="prefit",  # model already trained — just fit calibration layer
    )
    calibrated.fit(X_val, y_val)
    return calibrated


def plot_calibration_curve(
    calibrated_model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    n_bins: int = 10,
) -> plt.Figure:
    """Plot reliability diagram — essential for validating calibration quality.
    Log to MLflow as 'calibration_curve.png'.
    A well-calibrated model shows points near the diagonal.
    """
    prob_true, prob_pred = calibration_curve(
        y_test,
        calibrated_model.predict_proba(X_test)[:, 1],
        n_bins=n_bins,
    )
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(prob_pred, prob_true, marker="o", label="Model")
    ax.plot([0, 1], [0, 1], linestyle="--", label="Perfect calibration")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Fraction of positives")
    ax.set_title("Calibration Curve (Reliability Diagram)")
    ax.legend()
    return fig
```

---

## 8. Explainability (SHAP)

Every score in production must have SHAP values. This is a hard product requirement (ADR-003).

```python
# ml/scoring/explainability.py

import shap
from ml.features.feature_vector import FEATURE_COLUMNS

def compute_shap_values(
    model: xgb.XGBClassifier,
    feature_vector: np.ndarray,
    feature_names: list[str] = FEATURE_COLUMNS,
) -> list[SignalContribution]:
    """Compute SHAP values and return as list of SignalContribution objects.
    
    Uses TreeExplainer (not KernelExplainer) — fast, exact for tree models.
    
    Returns all signals sorted by abs(contribution) descending.
    Caller is responsible for taking top 5 for top_drivers.
    """
    explainer = shap.TreeExplainer(model)
    shap_vals = explainer.shap_values(feature_vector.reshape(1, -1))[0]
    
    contributions = []
    for i, (name, shap_val) in enumerate(zip(feature_names, shap_vals)):
        raw_value = feature_vector[i]
        contribution_points = float(shap_val * 100)  # scale to score points
        
        contributions.append(SignalContribution(
            signal_name=name,
            display_name=SIGNAL_DISPLAY_NAMES.get(name, name.replace("_", " ").title()),
            category=get_signal_category(name),
            raw_value=None if np.isnan(raw_value) else float(raw_value),
            contribution=round(contribution_points, 1),
            direction="increases_risk" if shap_val > 0 else "decreases_risk" if shap_val < 0 else "neutral",
            explanation=generate_explanation(name, raw_value, shap_val),
        ))
    
    return sorted(contributions, key=lambda x: abs(x.contribution), reverse=True)


# Human-readable display names for signals
SIGNAL_DISPLAY_NAMES = {
    "altman_z_score":               "Financial Stress Index (Altman Z')",
    "going_concern_flag":           "Going Concern Opinion",
    "current_ratio":                "Liquidity (Current Ratio)",
    "news_negative_count_30d":      "Negative News Volume (30 days)",
    "topic_bankruptcy_30d":         "Bankruptcy-Related News",
    "topic_layoff_30d":             "Layoff-Related News",
    "shipping_volume_delta_30d":    "Shipping Volume Change (30 days)",
    "shipping_anomaly_flag":        "Shipping Anomaly Detected",
    "on_sanctions_list":            "OFAC Sanctions Hit",
    "country_risk_score":           "Country Political Risk",
    "industry_pmi":                 "Industry PMI",
    "high_yield_spread_delta_30d":  "Credit Market Stress",
    # ... add all 46 features
}
```

---

## 9. Label Collection Strategy

Labels are the scarcest resource. Collect them systematically from day one.

### 9.1 Sources (Priority Order)

```
1. In-app disruption reporting (highest quality)
   UI: Alert detail page → "Did this supplier cause a disruption?" button
   On click: open form with fields:
     - disruption_type: enum (delivery_failure, quality_issue, capacity_reduction, other)
     - disruption_date: date (when it was discovered)
     - severity: low/medium/high/critical
     - description: text (optional, max 500 chars)
   
   Stored in: postgres.disruption_reports table
   
2. Alert resolution feedback
   When alert status → "resolved": optional prompt
   "Was there an actual supply disruption? Yes / No / Uncertain"
   Yes → auto-create disruption_report record

3. News NLP extraction (automated, requires validation)
   Articles with topic_bankruptcy=True or topic_disaster=True and high credibility
   → Queue for human review in admin dashboard
   Validated → label = 1, rejected → label = 0 with note

4. Proxy labels (lowest quality, use with caution)
   shipping_volume_delta_30d < -0.8 for 2 consecutive periods
   → Flagged as "proxy_disruption", confidence = 0.6
   → Used in training only if validated_labels < 50
```

### 9.2 Label Storage Schema

```sql
-- postgres: disruption_reports
CREATE TABLE disruption_reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id         VARCHAR(30) NOT NULL REFERENCES suppliers(id),
    tenant_id           UUID REFERENCES tenants(id),     -- NULL if auto-detected
    disruption_type     VARCHAR(50) NOT NULL,
    disruption_date     DATE NOT NULL,
    discovery_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    severity            VARCHAR(10) NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    description         TEXT,
    source              VARCHAR(20) NOT NULL CHECK (
                            source IN ('user_reported', 'alert_resolution',
                                       'news_validated', 'proxy')
                        ),
    confidence          NUMERIC(3,2) NOT NULL DEFAULT 1.0,  -- 0.6 for proxy
    validated_by        UUID REFERENCES users(id),          -- NULL if auto
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_disruption_supplier_date ON disruption_reports(supplier_id, disruption_date);
```

### 9.3 Training Label Creation

```python
# ml/training/label_builder.py

def build_training_labels(
    feature_dates: pd.DataFrame,        # supplier_id × feature_date
    disruption_reports: pd.DataFrame,   # from disruption_reports table
    horizon_days: int = 90,
    min_confidence: float = 0.8,        # exclude low-confidence proxy labels
) -> pd.Series:
    """Build binary labels for training.
    
    Label = 1 if a disruption was reported within horizon_days AFTER feature_date.
    Label = 0 otherwise (censored — no disruption observed in window).
    
    Temporal constraint: disruption_date must be STRICTLY AFTER feature_date.
    Disruptions before feature_date are not labels — they're history.
    """
    ...
```

---

## 10. Evaluation Metrics

### 10.1 Statistical Metrics

| Metric | Minimum Threshold | Target | Why |
|---|---|---|---|
| AUC-ROC | 0.70 | 0.80 | Standard classification quality |
| PR-AUC | 0.25 | 0.40 | More informative than AUC-ROC for imbalanced data |
| Precision @ top 10% risk | 30% | 50% | Procurement acts on the top 10% — precision there matters most |
| Recall @ score > 70 | 40% | 60% | Must catch most real disruptions above the alert threshold |
| Brier Score | < 0.15 | < 0.10 | Calibration quality |

### 10.2 Business Metric (Most Important)

**% of actual disruptions where score > 60 at least 14 days before the event.**

Target: 50% at launch, 70% at 12 months.

This is the metric customers actually care about. Track it separately from statistical metrics.

### 10.3 Evaluation Code

```python
# ml/evaluation/metrics.py

from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
import numpy as np

def evaluate_model(
    model,
    X_val: np.ndarray,
    y_val: np.ndarray,
    X_train: np.ndarray,
    y_train: np.ndarray,
) -> dict[str, float]:
    """Compute all evaluation metrics. Returns dict for MLflow logging."""
    val_proba = model.predict_proba(X_val)[:, 1]
    train_proba = model.predict_proba(X_train)[:, 1]
    
    # Overfit check: val AUC should be within 10% of train AUC
    train_auc = roc_auc_score(y_train, train_proba)
    val_auc   = roc_auc_score(y_val, val_proba)
    overfit_ratio = val_auc / train_auc
    
    # Precision at top 10%
    n_top = max(1, int(len(y_val) * 0.10))
    top_indices = np.argsort(val_proba)[-n_top:]
    precision_top10 = y_val[top_indices].mean()
    
    # Recall at score > 70 (score = proba * 100)
    high_risk_mask = val_proba >= 0.70
    recall_at_70 = (
        y_val[high_risk_mask].sum() / max(y_val.sum(), 1)
        if high_risk_mask.any() else 0.0
    )
    
    return {
        "val_auc_roc":        val_auc,
        "val_pr_auc":         average_precision_score(y_val, val_proba),
        "val_brier_score":    brier_score_loss(y_val, val_proba),
        "precision_top_10pct": precision_top10,
        "recall_at_score_70": recall_at_70,
        "train_auc_roc":      train_auc,
        "overfit_ratio":      overfit_ratio,
    }
```

---

## 11. MLOps Pipeline

### 11.1 Directory Structure

```
ml/
├── features/
│   ├── feature_vector.py       # SupplierFeatureVector Pydantic model
│   ├── financial.py            # Altman Z-Score, ratio computation
│   ├── news.py                 # Sentiment aggregation
│   ├── shipping.py             # Volume delta, z-score
│   └── utils.py                # safe_ratio, safe_delta
├── training/
│   ├── splits.py               # Temporal train/val/test split
│   ├── label_builder.py        # Build training labels from disruption reports
│   ├── xgb_trainer.py          # XGBoost training + MLflow
│   ├── anomaly_trainer.py      # Isolation Forest training
│   └── calibration.py          # Platt/isotonic calibration
├── scoring/
│   ├── heuristic_scorer.py     # Rules-based v0 model
│   ├── ensemble.py             # Score combination formula
│   ├── explainability.py       # SHAP computation
│   ├── run_scoring.py          # Airflow-callable scoring entry point
│   └── score_repository.py     # Repository pattern for score storage
└── evaluation/
    ├── metrics.py              # Statistical metrics
    └── monitoring.py           # Drift detection
```

### 11.2 MLflow Experiment Structure

```
Experiments:
  supplier_risk_heuristic/     → heuristic v0 runs (no training, just params)
  supplier_risk_xgb/           → XGBoost training runs
  supplier_risk_ensemble/      → Combined model evaluation runs
  # supplier_risk_cox/         → reserved for future Cox PH survival model (500+ labelled events needed)

Model Registry stages:
  None → Staging → Production → Archived

Promotion rules:
  Staging:    Automated — any run that passes minimum thresholds
  Production: Manual approval required + comparison to current production model
  Never:      Promote a model with val_auc_roc < 0.70 or val_pr_auc < 0.25
```

### 11.3 Drift Detection

```python
# ml/evaluation/monitoring.py

from scipy.stats import ks_2samp

def detect_score_distribution_drift(
    baseline_scores: list[float],   # last 4 weeks
    current_scores: list[float],    # current week
    threshold: float = 0.05,
) -> dict:
    """KS test on score distributions. p < threshold = significant drift."""
    stat, p_value = ks_2samp(baseline_scores, current_scores)
    drift_detected = p_value < threshold
    return {
        "ks_statistic": stat,
        "p_value": p_value,
        "drift_detected": drift_detected,
        "action": "trigger_retraining" if drift_detected else "no_action",
    }


def detect_feature_importance_drift(
    baseline_importances: dict[str, float],   # from training
    current_importances: dict[str, float],    # from current week predictions
    threshold: float = 0.15,
) -> list[str]:
    """Detect features whose importance has shifted significantly.
    
    A feature going dark (e.g. data source outage) shows up as
    importance dropping to near zero. Flag it.
    
    Returns list of feature names with significant drift.
    """
    drifted = []
    for feature, baseline_imp in baseline_importances.items():
        current_imp = current_importances.get(feature, 0.0)
        delta = abs(current_imp - baseline_imp)
        if delta > threshold:
            drifted.append(feature)
    return drifted
```

### 11.4 Retraining Triggers

```
Trigger 1: Weekly KS test (Airflow DAG: detect_score_drift, every Monday 08:00 UTC)
           → If p_value < 0.05: trigger retraining pipeline

Trigger 2: Performance degradation (weekly evaluation on rolling window)
           → If val_pr_auc < 0.20: trigger retraining + alert ML team

Trigger 3: New labels available
           → If new disruption_reports count > 20 since last training: trigger retraining

Trigger 4: Manual trigger
           → python -m ml.training.xgb_trainer --force-retrain

Trigger 5: Feature importance drift
           → If any feature importance drops > 0.15 from baseline: investigate data source
             (may indicate upstream data pipeline failure, not model failure)
```

---

## 12. Feature Importance Monitoring

Track feature importances over time — a dropping importance often signals a data source failure,
not a model problem.

```python
def log_feature_importances_weekly(
    model: xgb.XGBClassifier,
    X_week: np.ndarray,
    feature_names: list[str],
) -> dict[str, float]:
    """Compute SHAP-based feature importances for current week's predictions.
    
    Store in: scores.model_metadata (feature_importance VARIANT column)
    Compare against: training-time importances
    Alert if: any feature drops > 0.15 from training baseline (see Section 11.3)
    """
    explainer = shap.TreeExplainer(model)
    shap_vals = np.abs(explainer.shap_values(X_week)).mean(axis=0)
    total = shap_vals.sum()
    importances = {
        name: float(val / total)
        for name, val in zip(feature_names, shap_vals)
    }
    return importances
```

---

*See DATA_SOURCES.md for where each feature signal originates.*
*See ARCHITECTURE.md Section 6 for Feature Store (Postgres pipeline.supplier_feature_vector).*
*See DECISIONS.md ADR-003 for model choice rationale.*
*See DECISIONS.md ADR-007 for heuristic-first strategy.*
*See DECISIONS.md ADR-014 for frozen RiskScoreOutput schema.*
