# DATA_SOURCES.md — Data Sources & Ingestion Specifications

> Read this before writing ANY ingestion script, ETL job, or data model.
> If a data source behaves differently than documented here, stop and flag it — do not work around it silently.
> All ingestion code lives in `data/ingestion/`. All Kafka schemas are Pydantic models in `data/ingestion/{source}/models.py`.

---

## Quick Reference

| Source | Purpose | Cost | Auth | Refresh | Ingestion Type |
|---|---|---|---|---|---|
| SEC EDGAR | Financial stress signals | Free | User-Agent header | Daily | Incremental (new filings only) |
| NewsAPI | News sentiment + events | Free tier / ~$449/mo paid | API key | Every 2h | Incremental (last 24h) |
| GDELT | News fallback | Free | None | Every 2h | Incremental (last 24h) |
| MarineTraffic | Shipping volume signals | $50–200/mo | API key | Every 4h | Incremental (new port calls) |
| PortWatch | Shipping fallback (free) | Free | None | Daily | Full replace |
| FRED | Macro/commodity signals | Free | API key | Daily | Incremental (new observations) |
| OFAC Sanctions | Sanctions screening | Free | None | Daily | Full replace |
| ACLED Conflict | Country conflict data | Free (registration) | API key | Daily | Incremental |
| NOAA Weather | Natural disaster signals | Free | None | Every 6h | Incremental (active alerts) |
| USGS Earthquake | Earthquake signals | Free | None | Every 6h | Incremental |

---

## Source 1: SEC EDGAR

**Purpose:** Financial health signals — Altman Z-Score, going concern flags, liquidity ratios.
**Coverage:** All US public companies + foreign private issuers listed in the US (~10,000 companies).
**Limitation:** Public companies only. For private suppliers, fall back to proxy signals — see Section 9.
**Ingestion type:** Incremental. On first run, backfill last 3 years. Subsequent runs fetch new filings only.

### API Details

```
Base URL:   https://data.sec.gov/
Rate limit: 10 requests/second — enforced with asyncio.Semaphore(10)
Auth:       No API key. User-Agent header required by SEC Terms of Service:
            User-Agent: "SupplierRiskPlatform your-real-email@domain.com"
            Using a fake email risks IP ban. Use your real email.

Key endpoints:
  GET /submissions/{CIK}.json
    → Company filing history and metadata
    → CIK must be zero-padded to 10 digits: "0000789019" not "789019"

  GET /api/xbrl/companyfacts/{CIK}.json
    → Structured XBRL financial data for all filings
    → This is the primary endpoint — parse all financials from here

  GET /Archives/edgar/full-index/{year}/{quarter}/company.idx
    → Bulk index for backfill — all filings for a quarter
    → Use this for first-run backfill only, not daily incremental

  GET /cgi-bin/browse-edgar?action=getcompany&company={name}&type=10-K&output=atom
    → Search companies by name → returns CIK
    → Use for entity resolution: company name → CIK mapping
```

### XBRL Field Mappings (Critical — Multiple Concept Names Per Field)

XBRL has no standardisation — different companies use different concept names for the same financial field. Always try concepts in order and use the first non-null value.

```python
# data/ingestion/sec_edgar/xbrl_concepts.py

XBRL_FIELD_CONCEPTS = {
    "total_assets": [
        "Assets",
    ],
    "current_assets": [
        "AssetsCurrent",
    ],
    "total_liabilities": [
        "Liabilities",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "retained_earnings": [
        "RetainedEarningsAccumulatedDeficit",
        "RetainedEarnings",
    ],
    "shareholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital",
        "MembersEquity",
    ],
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromRelatedParties",
    ],
    "ebit": [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
    ],
    "long_term_debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermNotesPayable",
    ],
    "interest_expense": [
        "InterestExpense",
        "InterestAndDebtExpense",
        "FinancingInterestExpense",
    ],
    "inventory": [
        "InventoryNet",
        "InventoryFinishedGoodsAndWorkInProcess",
        "InventoryGross",
    ],
}

def get_latest_value(
    company_facts: dict,
    field_name: str,
    filing_type: str = "10-Q",  # prefer quarterly, fall back to annual
) -> float | None:
    """Try each concept name in order, return first non-null value.
    
    Prefers most recent quarterly filing. Falls back to annual if no quarterly.
    Returns None if no concept found — never returns 0 for missing data.
    """
    concepts = XBRL_FIELD_CONCEPTS.get(field_name, [])
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    
    for concept in concepts:
        if concept in us_gaap:
            units = us_gaap[concept].get("units", {})
            usd_values = units.get("USD", [])
            if usd_values:
                # Filter to desired filing type, sort by end date desc
                filtered = [v for v in usd_values if v.get("form") == filing_type]
                if not filtered and filing_type == "10-Q":
                    filtered = [v for v in usd_values if v.get("form") == "10-K"]
                if filtered:
                    latest = sorted(filtered, key=lambda x: x["end"], reverse=True)[0]
                    return float(latest["val"])
    return None  # Never return 0 — None means data is missing
```

### Going Concern Detection

Parse the full text of 10-K filings. Flag if any of these phrases appear (case-insensitive):

```python
GOING_CONCERN_PHRASES = [
    "substantial doubt about its ability to continue as a going concern",
    "substantial doubt exists about the company's ability to continue as a going concern",
    "raise substantial doubt about our ability to continue as a going concern",
    "going concern doubt",
    "ability to continue as a going concern",
    "conditions raise substantial doubt",
]

# 10-K text endpoint:
# GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession_number}/{primary_document}
# Parse the .htm file — strip HTML tags before phrase search
```

### Backfill Strategy (First Run Only)

```python
BACKFILL_YEARS = 3  # go back 3 years on first run

# First run detection: check if ingestion_log has any record for source='sec_edgar'
# If no record: backfill mode — fetch all filings from (today - 3 years)
# If record exists: incremental mode — fetch filings since last_successful_run

# Backfill uses bulk index files (faster than individual CIK calls):
# https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{1-4}/company.idx
# Parse index, filter to 10-K and 10-Q filings, fetch XBRL for each CIK

# Backfill rate: ~500 companies/hour at 10 req/sec (with XBRL fetch per company)
# Estimated backfill time for 10,000 companies: ~20 hours (run once, overnight)
```

### Rate Limit Handling

```python
# asyncio.Semaphore(10) — max 10 concurrent requests
# tenacity retry: wait_exponential(multiplier=1, min=2, max=60)
# Retry on: httpx.TimeoutException, HTTP 429, HTTP 503
# Do NOT retry on: HTTP 404 (company not found — log and skip)
# On 429: respect Retry-After header if present, else wait 60s
```

### Kafka Message Schema: `raw.sec`

```python
class SECRawEvent(BaseModel):
    source: Literal["sec_edgar"]
    cik: str                        # zero-padded to 10 digits
    company_name: str
    filing_type: Literal["10-K", "10-Q", "8-K"]
    filed_date: date
    period_of_report: date
    financials: dict[str, float | None]  # field_name → value, None if missing
    going_concern: bool
    filing_url: str                 # direct link to filing on SEC website
    ingested_at: datetime           # UTC
```

### Data Quality Validation

```python
class SECFilingValidator:
    def validate(self, event: SECRawEvent) -> list[str]:
        """Returns list of error strings. Empty list = valid."""
        errors = []
        if not event.cik:
            errors.append("cik_missing")
        if len(event.cik) != 10:
            errors.append(f"cik_not_padded: got {len(event.cik)} chars")
        if event.filed_date > date.today():
            errors.append(f"future_filed_date: {event.filed_date}")
        if event.period_of_report > date.today():
            errors.append(f"future_period: {event.period_of_report}")
        # total_assets must be positive IF present (None is OK)
        assets = event.financials.get("total_assets")
        if assets is not None and assets <= 0:
            errors.append(f"invalid_total_assets: {assets}")
        return errors

# On validation errors: route to raw.dlq.sec with error list attached
# On valid: publish to raw.sec
# Never use assert — raises silently disabled in production Python (-O flag)
```

---

## Source 2: NewsAPI + GDELT

**Purpose:** Real-time sentiment scoring and supply chain event detection.
**Primary:** NewsAPI.org — higher quality, paid
**Fallback:** GDELT — free, noisier, activate automatically when NewsAPI quota is exhausted
**Ingestion type:** Incremental. Fetch articles published in the last 26 hours (2h overlap to catch late-indexed articles).

### NewsAPI Details

```
Base URL:     https://newsapi.org/v2/
Auth:         X-Api-Key: {NEWS_API_KEY} header
Rate limits:
  Free tier:  100 requests/day, articles from last 30 days only
  Paid ($449/mo): unlimited requests, full historical archive
  MVP plan: start on paid — free tier is insufficient for real-time monitoring

Key endpoint:
  GET /everything
  Params:
    q={company_name}          — search query
    language=en               — English only
    sortBy=publishedAt        — newest first
    from={yesterday_iso}      — last 26 hours
    pageSize=100              — max per request
    page={n}                  — pagination

Pagination: continue fetching pages until articles[0].publishedAt < cutoff_time
Max articles per company per run: 500 (stop paginating after this — avoid runaway queries)
```

### GDELT Details

```
Base URL: https://api.gdeltproject.org/api/v2/doc/doc
Auth:     None
Free:     Yes — no rate limit documented, be respectful (max 1 req/sec)

Key endpoint:
  GET ?query={company_name}&mode=artlist&maxrecords=250&format=json&TIMESPAN=1440
  TIMESPAN=1440 = last 24 hours (in minutes)

Response schema (different from NewsAPI — handle both):
  {
    "articles": [
      {
        "url": "https://...",
        "title": "...",
        "seendate": "20250304T100000Z",  ← format: YYYYMMDDTHHmmSSZ (not ISO 8601)
        "domain": "reuters.com",
        "language": "English",
        "sourcecountry": "United States"
      }
    ]
  }

Note: GDELT does NOT return article content — only URL and metadata.
For content, fetch the URL directly (best effort — many will 403/paywall).
Use title-only sentiment if content is unavailable.
```

### Deduplication Strategy

```python
def compute_article_id(url: str) -> str:
    """Deterministic article ID — same URL always produces same ID."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

# Before writing to Postgres: check if article_id already exists
# SELECT COUNT(*) FROM pipeline.raw_news_articles WHERE article_id = {id}
# If exists: skip (upsert semantics — do not duplicate)
# This handles the case where NewsAPI and GDELT return the same article
```

### Source Credibility Scores

```python
# data/ingestion/news/credibility.py

SOURCE_CREDIBILITY: dict[str, float] = {
    # Tier 1 — major wire services and financial press (1.0)
    "reuters.com": 1.0,
    "bloomberg.com": 1.0,
    "apnews.com": 1.0,
    "ft.com": 1.0,
    "wsj.com": 1.0,
    "economist.com": 1.0,
    # Tier 2 — reputable business press (0.85)
    "cnbc.com": 0.85,
    "forbes.com": 0.85,
    "fortune.com": 0.85,
    "businessinsider.com": 0.80,
    "techcrunch.com": 0.80,
    # Tier 3 — trade press and regional (0.65)
    "supplychainbrain.com": 0.70,
    "logisticsmgmt.com": 0.70,
    "manufacturingtomorrow.com": 0.65,
    # Default for unknown sources
    "default": 0.50,
}

def get_credibility(domain: str) -> float:
    """Extract domain from URL and look up credibility score."""
    # Strip www. prefix, get base domain
    base = domain.replace("www.", "").lower()
    return SOURCE_CREDIBILITY.get(base, SOURCE_CREDIBILITY["default"])
```

### NLP Processing Pipeline

```
Step 1: Language filter
  Skip non-English articles (GDELT tags language, NewsAPI filtered at query time)

Step 2: Deduplication check
  Compute article_id = sha256(url)
  Skip if already in Postgres pipeline.raw_news_articles

Step 3: Entity linking (lightweight — full resolution in entity_resolution.py)
  Run spaCy NER (en_core_web_sm) on title + first 500 chars of content
  Extract ORG entities
  Fuzzy match each ORG against supplier registry (rapidfuzz threshold 80)
  Assign supplier_id if match found, else supplier_id = NULL

Step 4: FinBERT sentiment
  Input: title + content (truncated to 512 tokens)
  If content unavailable (GDELT): use title only
  Model: ProsusAI/finbert (3 classes: positive/negative/neutral)
  Output: sentiment_score (-1.0 to 1.0), sentiment_label
  Fallback: lexicon scorer (see below) if model unavailable or OOM

Step 5: Topic classification (keyword-based in Phase 1, ML classifier in Phase 2)
  Input: title + content lowercased
  Output: boolean flags per topic

Step 6: Write enriched record to Postgres pipeline.stg_news_sentiment
```

### Topic Keywords

```python
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "layoff": [
        "layoff", "layoffs", "laid off", "redundan", "retrench",
        "workforce reduction", "job cut", "headcount reduction",
        "downsizing", "right-sizing", "position eliminated",
    ],
    "bankruptcy": [
        "bankruptcy", "bankrupt", "chapter 11", "chapter 7",
        "insolvency", "insolvent", "administration", "liquidat",
        "receivership", "going concern", "debt restructur",
    ],
    "strike": [
        "strike", "strikes", "striking", "industrial action",
        "walkout", "work stoppage", "labor dispute", "labour dispute",
        "union action", "picket",
    ],
    "disaster": [
        "fire", "explosion", "flood", "flooding", "earthquake",
        "hurricane", "typhoon", "tornado", "facility damage",
        "plant damage", "factory fire", "force majeure",
    ],
    "regulatory": [
        "fined", "penalty", "penalties", "recall", "recalled",
        "shutdown order", "cease and desist", "violation",
        "regulatory action", "investigation", "subpoena",
        "export ban", "import ban",
    ],
}

def classify_topics(text: str) -> dict[str, bool]:
    """Case-insensitive keyword matching. Returns True if ANY keyword matches."""
    text_lower = text.lower()
    return {
        topic: any(kw in text_lower for kw in keywords)
        for topic, keywords in TOPIC_KEYWORDS.items()
    }
```

### FinBERT Fallback (Lexicon Scorer)

```python
# Used when: model file not found, GPU OOM, or model load fails
# NOT used in production — only in local dev without GPU or in tests

NEGATIVE_WORDS = [
    "bankrupt", "layoff", "loss", "losses", "decline", "fail", "failure",
    "risk", "debt", "warn", "warning", "recall", "shutdown", "strike",
    "investigation", "penalty", "fine", "delay", "shortage", "disruption",
]
POSITIVE_WORDS = [
    "profit", "growth", "expand", "expansion", "award", "record",
    "strong", "beat", "exceed", "partnership", "contract", "win",
    "revenue", "increase", "improve",
]

def lexicon_sentiment(text: str) -> tuple[float, str]:
    words = text.lower().split()
    neg = sum(1 for w in words if any(n in w for n in NEGATIVE_WORDS))
    pos = sum(1 for w in words if any(p in w for p in POSITIVE_WORDS))
    total = max(len(words), 1)
    score = (pos - neg) / total * 10  # scale to approx -1 to 1 range
    score = max(-1.0, min(1.0, score))
    label = "negative" if score < -0.1 else "positive" if score > 0.1 else "neutral"
    return score, label
```

### Kafka Message Schema: `raw.news`

```python
class NewsRawEvent(BaseModel):
    source: Literal["newsapi", "gdelt"]
    article_id: str                 # sha256(url) — deterministic
    url: str
    title: str
    content: str | None             # None for GDELT or paywalled articles
    published_at: datetime          # UTC — parse GDELT's YYYYMMDDTHHmmSSZ format
    source_name: str                # e.g. "Reuters"
    source_domain: str              # e.g. "reuters.com"
    source_credibility: float       # 0.0–1.0
    ingested_at: datetime           # UTC
    ingestion_source: Literal["newsapi", "gdelt"]
```

### Enriched Schema: `pipeline.stg_news_sentiment`

```sql
article_id              VARCHAR(64)     NOT NULL    -- sha256 hex
supplier_id             VARCHAR(30)                 -- NULL if unresolved
supplier_name_raw       VARCHAR(500)                -- extracted NER entity
title                   VARCHAR(1000)   NOT NULL
url                     VARCHAR(2000)   NOT NULL
published_at            TIMESTAMPTZ     NOT NULL
source_name             VARCHAR(255)    NOT NULL
source_domain           VARCHAR(255)    NOT NULL
source_credibility      FLOAT           NOT NULL    -- 0.0 to 1.0
sentiment_score         FLOAT           NOT NULL    -- -1.0 to 1.0
sentiment_label         VARCHAR(10)     NOT NULL    -- positive/negative/neutral
sentiment_model         VARCHAR(50)     NOT NULL    -- 'finbert' or 'lexicon_fallback'
topic_layoff            BOOLEAN         NOT NULL    DEFAULT FALSE
topic_bankruptcy        BOOLEAN         NOT NULL    DEFAULT FALSE
topic_strike            BOOLEAN         NOT NULL    DEFAULT FALSE
topic_disaster          BOOLEAN         NOT NULL    DEFAULT FALSE
topic_regulatory        BOOLEAN         NOT NULL    DEFAULT FALSE
word_count              INTEGER         NOT NULL
content_available       BOOLEAN         NOT NULL    -- FALSE for GDELT title-only
ingestion_source        VARCHAR(10)     NOT NULL    -- 'newsapi' or 'gdelt'
processed_at            TIMESTAMPTZ     NOT NULL
_dbt_ingested_at        TIMESTAMPTZ     NOT NULL
```

---

## Source 3: MarineTraffic (AIS Data)

**Purpose:** Shipping volume signals — sustained drop in port activity is a leading indicator of reduced production.
**Coverage:** Global port vessel movements.
**Cost:** ~$50–200/month. Use the "Port Activity" API tier at minimum.
**MVP fallback:** PortWatch (World Bank, free) if budget not approved yet.
**Ingestion type:** Incremental. Fetch new port calls since last run timestamp.

### API Details

```
Base URL: https://services.marinetraffic.com/api/
Auth:     ?v=8&apikey={MARINETRAFFIC_KEY} — query param (not header)

Key endpoints:
  GET /portcalls/portid:{port_id}/fromdate:{date}/todate:{date}
    → All vessel calls at a port between two dates
    → Response: list of {vessel_mmsi, vessel_name, arrival, departure, cargo_type}

  GET /expectedarrivals/portid:{port_id}
    → Vessels expected to arrive in next 48h
    → Used for early warning of unusual drops in expected traffic

Rate limit: depends on subscription tier. Assume 1 req/sec to be safe.
Timeout: set 30s timeout — MarineTraffic can be slow
```

### Supplier → Port Mapping

This mapping is the most fragile part of the AIS pipeline. Wrong port = wrong signal for that supplier.

```sql
-- Table: supplier_ports (in Postgres operational DB)
CREATE TABLE supplier_ports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id     VARCHAR(30) NOT NULL REFERENCES suppliers(id),
    port_id         VARCHAR(50) NOT NULL,           -- MarineTraffic port ID
    port_name       VARCHAR(255) NOT NULL,
    country         CHAR(2) NOT NULL,               -- ISO 3166-1 alpha-2
    is_primary      BOOLEAN NOT NULL DEFAULT TRUE,
    confidence      NUMERIC(3,2) NOT NULL,           -- 0.00 to 1.00
    source          VARCHAR(20) NOT NULL             -- 'manual', 'address_geocode', 'inferred'
                    CHECK (source IN ('manual', 'address_geocode', 'inferred')),
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_supplier_ports_primary ON supplier_ports(supplier_id)
    WHERE is_primary = TRUE;
```

**Port mapping sources (in priority order):**
1. Manual — human verified (confidence: 1.0)
2. Address geocode — supplier address → nearest major port (confidence: 0.7)
3. Inferred — from SEC filings geographic segment data (confidence: 0.5)

**Rule:** Only use port calls from ports with confidence ≥ 0.7. Flag lower-confidence mappings in the score's `data_completeness` field.

### PortWatch Fallback (Free — World Bank)

```
URL: https://portwatch.imf.org/
Data: Weekly port call statistics by port and vessel type
Download: CSV exports available at country/port level
Coverage: ~1,000 major global ports
Limitation: Weekly granularity only (not event-level like MarineTraffic)
Use when: MarineTraffic budget not approved

Ingestion: Weekly CSV download → parse → write to raw.ais_events with source='portwatch'
```

### Kafka Message Schema: `raw.ais`

```python
class AISRawEvent(BaseModel):
    source: Literal["marinetraffic", "portwatch"]
    port_id: str
    port_name: str
    country: str                    # ISO 3166-1 alpha-2
    vessel_mmsi: str | None         # None for PortWatch (aggregated data)
    vessel_name: str | None
    arrival_time: datetime          # UTC
    departure_time: datetime | None # None if vessel still in port
    cargo_type: str | None          # 'container', 'bulk', 'tanker', etc.
    dwell_hours: float | None       # computed: (departure - arrival) in hours
    ingested_at: datetime           # UTC
```

---

## Source 4: FRED (Federal Reserve Economic Data)

**Purpose:** Macroeconomic context signals — commodity prices, credit spreads, manufacturing PMI.
**Coverage:** US-centric, but commodity prices are global.
**Cost:** Free. API key required (register at fred.stlouisfed.org).
**Ingestion type:** Incremental. FRED updates most series daily — fetch only new observations.

### API Details

```
Base URL: https://api.stlouisfed.org/fred/
Auth:     ?api_key={FRED_API_KEY}&file_type=json — query param

Key endpoint:
  GET /series/observations
  Params:
    series_id={ID}
    observation_start={last_fetched_date}  ← incremental — only new data
    sort_order=desc
    limit=100
    file_type=json

No documented rate limit — use 1 req/sec to be respectful.
```

### Series We Track

```python
# data/ingestion/macro/fred_series.py

FRED_SERIES = {
    # Energy
    "DCOILWTICO": {
        "name": "Crude Oil Price (WTI)",
        "unit": "USD/barrel",
        "frequency": "daily",
        "relevant_industries": ["automotive", "chemicals", "textiles", "default"],
    },
    # Metals / manufacturing inputs
    "PPIACO": {
        "name": "Producer Price Index (All Commodities)",
        "unit": "index",
        "frequency": "monthly",
        "relevant_industries": ["all"],
    },
    # Agricultural
    "PWHEAMTUSDM": {
        "name": "Wheat Price (World Bank)",
        "unit": "USD/mt",
        "frequency": "monthly",
        "relevant_industries": ["food_beverage"],
    },
    # Credit stress
    "BAMLH0A0HYM2": {
        "name": "ICE BofA US High Yield OAS",
        "unit": "percent",
        "frequency": "daily",
        "relevant_industries": ["all"],  # systemic credit stress signal
    },
    # Manufacturing activity
    "MANEMP": {
        "name": "Manufacturing Employees",
        "unit": "thousands",
        "frequency": "monthly",
        "relevant_industries": ["all"],
    },
    # Economic health
    "PAYEMS": {
        "name": "Nonfarm Payrolls",
        "unit": "thousands",
        "frequency": "monthly",
        "relevant_industries": ["all"],
    },
    # Semiconductor (electronics supply chain)
    "PCU334413334413": {
        "name": "PPI Semiconductors",
        "unit": "index",
        "frequency": "monthly",
        "relevant_industries": ["electronics"],
    },
}
```

### Industry → Commodity Mapping

```python
INDUSTRY_COMMODITY_MAP: dict[str, list[str]] = {
    "automotive":       ["DCOILWTICO", "PPIACO"],
    "electronics":      ["PPIACO", "PCU334413334413"],
    "food_beverage":    ["PPIACO", "PWHEAMTUSDM"],
    "chemicals":        ["DCOILWTICO", "PPIACO"],
    "textiles":         ["PPIACO", "DCOILWTICO"],
    "pharmaceutical":   ["PPIACO"],
    "construction":     ["PPIACO"],
    "default":          ["PPIACO", "BAMLH0A0HYM2"],
}

def get_relevant_series(industry_code: str | None) -> list[str]:
    """Map NAICS industry code to relevant FRED series IDs."""
    # NAICS mapping (first 2 digits)
    naics_to_industry = {
        "31": "food_beverage", "32": "chemicals", "33": "automotive",
        "33441": "electronics", "3254": "pharmaceutical",
        "23": "construction", "31-33": "textiles",
    }
    industry = naics_to_industry.get(str(industry_code)[:2], "default")
    return INDUSTRY_COMMODITY_MAP.get(industry, INDUSTRY_COMMODITY_MAP["default"])
```

### Kafka Message Schema: `raw.macro`

```python
class MacroRawEvent(BaseModel):
    source: Literal["fred"]
    series_id: str                  # e.g. "DCOILWTICO"
    series_name: str                # human-readable name
    observation_date: date
    value: float | None             # None if FRED reports missing observation
    unit: str                       # "USD/barrel", "index", "percent", etc.
    ingested_at: datetime           # UTC
```

---

## Source 5: Geopolitical Risk Feeds

**Purpose:** Country-level sanctions, conflict intensity, and political stability signals.

### 5a: OFAC Sanctions List

```
URL:       https://www.treasury.gov/ofac/downloads/sdn.xml
Format:    XML — Specially Designated Nationals list
Auth:      None
Cost:      Free
Frequency: Daily full replace (OFAC republishes the full list daily)
Ingestion: Download full XML → parse → replace raw.geo_events WHERE source='ofac'

Key fields to extract:
  <sdnEntry>
    <lastName>   → company/entity name
    <sdnType>    → 'Entity' (companies) or 'Individual' (persons — skip)
    <programList> → sanctions program (e.g. IRAN, RUSSIA, DPRK)
    <akaList>    → alternative names (important for entity resolution)
    <addressList> → country
  </sdnEntry>

After parsing: fuzzy match entity names against our supplier registry
Match threshold: 90 (higher than normal — false positives are very costly here)
On match: set on_sanctions_list = TRUE in supplier record immediately
On match: fire alert immediately regardless of score threshold
```

### 5b: ACLED Conflict Data

```
URL:   https://api.acleddata.com/acled/read
Auth:  ?key={ACLED_KEY}&email={your_email} — free registration required
Cost:  Free for research/commercial use under 1M rows/month

Key endpoint:
  GET /acled/read?key={KEY}&email={EMAIL}&country={country}&limit=1000
  &fields=event_date|country|event_type|fatalities|sub_event_type

event_type values we track:
  'Battles'               → high severity
  'Explosions/Remote violence' → high severity
  'Violence against civilians' → medium severity
  'Protests'              → low severity (monitor only)
  'Riots'                 → medium severity

Aggregation (computed in dbt, not ingestion):
  Monthly conflict intensity score per country = 
    weighted sum of events by severity / population (normalised 0–100)

Ingestion type: Incremental — fetch events since last run date
```

### 5c: Country Risk Composite Score

Computed in dbt from multiple sources — not a single API.

```
Component 1: ACLED conflict intensity (40% weight)
  → events per month, weighted by severity

Component 2: OFAC sanctions density (30% weight)  
  → count of sanctioned entities in country / total entities

Component 3: World Bank Political Stability Index (30% weight)
  → Annual publication. Download CSV once per year.
  → URL: https://databank.worldbank.org/source/worldwide-governance-indicators

Final score: 0–100 (higher = more risk)
Computed in: marts.supplier_geo_features (dbt model)
Updated: Daily (ACLED and OFAC components) / Annually (World Bank)
```

### Kafka Message Schema: `raw.geo`

```python
class GeoRawEvent(BaseModel):
    source: Literal["ofac", "acled", "world_bank"]
    event_type: str                 # 'sanctions_entry', 'conflict_event', 'stability_index'
    country: str                    # ISO 3166-1 alpha-2
    entity_name: str | None         # for OFAC entries
    event_date: date
    severity: str | None            # 'low', 'medium', 'high', 'critical'
    raw_data: dict                  # full source record as JSON
    ingested_at: datetime           # UTC
```

---

## Source 6: Weather & Natural Disaster Feeds

**Purpose:** Flag suppliers near active natural disasters — hurricanes, floods, earthquakes.
**Ingestion type:** Incremental. Fetch active alerts every 6 hours.

### 6a: NOAA Weather Alerts

```
Base URL: https://api.weather.gov/
Auth:     None. User-Agent header required:
          User-Agent: "SupplierRiskPlatform your-email@domain.com"

Key endpoint:
  GET /alerts/active?status=actual&message_type=alert&urgency=Immediate,Expected
  Response: GeoJSON FeatureCollection
  Each feature: {geometry: polygon, properties: {event, severity, areaDesc, sent}}

Severity levels we act on:
  'Extreme'  → flag all suppliers within 100km
  'Severe'   → flag all suppliers within 150km
  'Moderate' → log only, no flag

Event types we track:
  Hurricane Warning, Tropical Storm Warning, Tornado Warning,
  Flash Flood Emergency, Blizzard Warning, Ice Storm Warning
```

### 6b: USGS Earthquake Feed

```
Base URL: https://earthquake.usgs.gov/fdsnws/event/1/
Auth:     None

Key endpoint:
  GET /query?format=geojson&minmagnitude=5.5&orderby=time&limit=100
  &starttime={last_check_iso}

Magnitude thresholds:
  ≥ 7.0  → flag suppliers within 300km (major — widespread infrastructure damage)
  6.0–6.9 → flag suppliers within 200km (strong — potential facility damage)
  5.5–5.9 → flag suppliers within 100km (moderate — monitor only)
```

### Geo-Matching Logic

Supplier location data comes from the `suppliers.primary_location` JSONB field in Postgres.
This field is populated during entity resolution using:
1. SEC filing address (for US public companies)
2. Company website scraping (best effort)
3. Manual entry (for key suppliers)

```python
from math import radians, sin, cos, sqrt, atan2

def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in km."""
    R = 6371  # Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def get_affected_suppliers(
    event_lat: float,
    event_lon: float,
    radius_km: float,
    all_suppliers: list[SupplierLocation],
) -> list[str]:
    """Return supplier_ids within radius_km of event coordinates."""
    return [
        s.supplier_id for s in all_suppliers
        if s.lat is not None and s.lon is not None
        and haversine_distance_km(event_lat, event_lon, s.lat, s.lon) <= radius_km
    ]
```

### Kafka Message Schema: `raw.geo` (weather events)

```python
class WeatherRawEvent(BaseModel):
    source: Literal["noaa", "usgs"]
    event_type: str                 # 'hurricane_warning', 'earthquake', etc.
    severity: Literal["moderate", "severe", "extreme"]
    latitude: float
    longitude: float
    radius_km: float                # impact radius based on severity
    affected_area_description: str  # human-readable area name
    event_time: datetime            # UTC — when event started
    ingested_at: datetime           # UTC
```

---

## Section 7: Private Company Strategy

~30% of mid-market suppliers are private companies not covered by SEC EDGAR. Do not skip them — use these proxy signals instead.

```
Private company signal priority (highest to lowest quality):

1. News signals (same as public companies)
   → NewsAPI + GDELT cover private companies in news
   → Often the BEST signal for private companies

2. Shipping / AIS signals (same as public companies)
   → Port activity doesn't distinguish public vs private
   → Full coverage if we have the port mapping

3. Geopolitical signals (same as public companies)
   → Country risk, sanctions screening — applies to all companies

4. OFAC sanctions check (same as public companies)
   → Many private companies appear on sanctions lists

5. Alternative financial proxies (private-company specific):
   → Dun & Bradstreet (D&B) Paydex Score — payment behaviour
     API: https://developer.dnb.com/ (paid, ~$100/mo for basic access)
     Paydex < 70 = elevated payment risk
   → Companies House (UK private companies — free)
     API: https://developer.company-information.service.gov.uk/
     → Statutory accounts, filing history, officer changes
   → State business registry filings (US, state-level — free, inconsistent)

For MVP:
  - Score private companies on news + shipping + geo + macro signals only
  - Set financial_data_is_stale = TRUE for all private companies
  - Set data_completeness to reflect missing financial signals
  - Add D&B integration to Phase 2 backlog (ADR needed)
```

---

## Section 8: Entity Resolution — The Critical Layer

Every ingestion pipeline produces raw company name strings. These must be resolved to canonical `supplier_id` before any downstream processing.

**Why this matters:** If "Apple Inc", "Apple", "Apple Computer", and "AAPL" are not resolved to the same `supplier_id`, we get fragmented signals and incorrect scores.

### Three-Stage Pipeline

```
Stage 1: Exact match (covers ~60% of cases)
  Normalise both strings:
    - Lowercase
    - Strip punctuation
    - Remove legal suffixes: inc, ltd, llc, corp, co, plc, ag, gmbh, sa, bv, ab, holdings, group
    - Collapse whitespace
  Match against canonical_name AND all aliases in supplier registry
  Confidence: 1.0
  Speed: microseconds (in-memory dict lookup)

Stage 2: Fuzzy match (covers ~25% of cases)
  Use rapidfuzz.fuzz.token_sort_ratio
  Threshold: 85 (lower to 80 if country_hint matches supplier country)
  Match against canonical_name AND aliases
  If multiple matches above threshold: pick highest score (with country_hint as tiebreaker)
  Confidence: match_ratio / 100
  Speed: milliseconds

Stage 3: LLM-assisted (covers ~10% of cases — hard cases only)
  Triggered when: Stage 2 produces candidates scoring 70–84
  Model: GPT-4o-mini (cheap — ~$0.0001 per resolution)
  Cost guard: max 200 LLM calls per day (env var: LLM_RESOLUTION_DAILY_LIMIT=200)
  Prompt template:
    "Is '{raw_name}' the same company as '{candidate_canonical_name}'?
     Additional context: {article_snippet_or_none}
     Answer with JSON only: {\"match\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"brief\"}"
  Confidence: from LLM response
  Speed: ~500ms

Unresolved (~5% of cases):
  If all stages fail: write to staging.unresolved_entities for manual review
  Return ResolutionResult(resolved=False, supplier_id=None)
  NEVER raise an exception — unresolved is a valid outcome
  NEVER assign wrong supplier_id — prefer unresolved over wrong match
```

### Legal Suffixes to Strip

```python
LEGAL_SUFFIXES = [
    # English
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "lp",
    "corp", "corporation", "co", "company", "plc",
    "holdings", "holding", "group", "international", "global",
    "enterprises", "ventures", "industries", "solutions", "technologies",
    # German
    "ag", "gmbh", "kg", "ohg",
    # French
    "sa", "sas", "sarl", "sasu",
    # Dutch/Belgian
    "bv", "nv",
    # Nordic
    "ab", "oy", "as", "asa",
    # Japanese (romanised)
    "kk", "kabushiki kaisha",
]
```

### Unresolved Entities Table

```sql
-- Postgres pipeline schema
CREATE TABLE pipeline.unresolved_entities (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    raw_name        VARCHAR(500)    NOT NULL,
    country_hint    CHAR(2),
    source          VARCHAR(20)     NOT NULL,   -- 'news', 'sec', 'ais', 'geo'
    context         VARCHAR(1000),              -- snippet where name appeared
    attempted_at    TIMESTAMPTZ     NOT NULL,
    attempts        INTEGER         NOT NULL DEFAULT 1,
    stage_reached   VARCHAR(10)     NOT NULL,   -- '1_exact', '2_fuzzy', '3_llm'
    best_candidate  VARCHAR(500),              -- best match found (if any), for manual review
    best_confidence FLOAT,
    resolved        BOOLEAN         NOT NULL DEFAULT FALSE,
    resolved_to     VARCHAR(30),               -- supplier_id if manually resolved
    resolved_at     TIMESTAMPTZ
);
```

---

## Section 9: Data Quality Rules

**Never use `assert` for validation** — Python's `-O` flag disables asserts in production. Use explicit validation with return values or exceptions.

### Validation Pattern

```python
class ValidationResult(BaseModel):
    is_valid: bool
    errors: list[str]           # empty if valid
    warnings: list[str]         # non-blocking issues

def validate_and_route(
    event: BaseModel,
    validator: Validator,
    producer: KafkaProducer,
    source: str,
) -> None:
    """Validate event and route to main topic or DLQ. Never raises."""
    result = validator.validate(event)
    if result.is_valid:
        producer.publish(f"raw.{source}", event)
        if result.warnings:
            log.warning("ingestion.validation_warning",
                       source=source, warnings=result.warnings)
    else:
        producer.publish_to_dlq(f"raw.{source}", event, result.errors)
        log.warning("ingestion.validation_failed",
                   source=source, errors=result.errors)
```

### Validation Rules Per Source

```python
# SEC EDGAR
SEC_RULES = [
    Rule("cik_present",          lambda e: e.cik is not None),
    Rule("cik_10_digits",        lambda e: len(e.cik) == 10),
    Rule("filed_date_not_future",lambda e: e.filed_date <= date.today()),
    Rule("period_not_future",    lambda e: e.period_of_report <= date.today()),
    Rule("assets_positive",      lambda e: e.financials.get("total_assets") is None
                                          or e.financials["total_assets"] > 0),
]

# News
NEWS_RULES = [
    Rule("article_id_present",   lambda e: bool(e.article_id)),
    Rule("article_id_sha256",    lambda e: len(e.article_id) == 64),
    Rule("url_present",          lambda e: e.url.startswith("http")),
    Rule("title_not_empty",      lambda e: len(e.title.strip()) > 5),
    Rule("published_recent",     lambda e: e.published_at >= datetime.utcnow() - timedelta(days=7)),
]

# AIS
AIS_RULES = [
    Rule("port_id_present",      lambda e: bool(e.port_id)),
    Rule("arrival_before_depart",lambda e: e.departure_time is None
                                          or e.arrival_time <= e.departure_time),
    Rule("arrival_not_future",   lambda e: e.arrival_time <= datetime.utcnow() + timedelta(hours=1)),
]
```

---

## Section 10: Refresh Rates & SLAs

| Source | Schedule | Ingestion Type | Latency SLA | Backfill on First Run |
|---|---|---|---|---|
| SEC EDGAR | Daily 02:00 UTC | Incremental (new filings) | < 4h after SEC publishes | 3 years |
| NewsAPI | Every 2h | Incremental (last 26h) | < 30 min | 30 days (paid plan) |
| GDELT | Every 2h (fallback) | Incremental (last 24h) | < 30 min | 1 year |
| MarineTraffic | Every 4h | Incremental (new port calls) | < 1h | 90 days |
| PortWatch | Weekly | Full replace | < 24h | Available |
| FRED | Daily 06:00 UTC | Incremental (new observations) | < 8h | 5 years |
| OFAC Sanctions | Daily 04:00 UTC | Full replace | < 4h | N/A (current list only) |
| ACLED Conflict | Daily 05:00 UTC | Incremental | < 12h | 2 years |
| NOAA Weather | Every 6h | Incremental (active alerts) | < 30 min | N/A (active only) |
| USGS Earthquake | Every 6h | Incremental (new events) | < 30 min | 90 days |

---

*Feature definitions derived from these sources: see ML_SPEC.md.*
*Postgres pipeline schema and dbt models: see ARCHITECTURE.md Section 5.*
*Entity resolution implementation: see SESSION_3.md and data/pipeline/entity_resolution.py.*
