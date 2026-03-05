# API_SPEC.md — REST API Specification

> Read this before building any API endpoint, middleware, or route handler.
> Every endpoint here is the contract between backend and frontend.
> If an endpoint behaves differently than documented, update this file in the same commit.

---

## 1. Principles

- All endpoints under `/api/v1/`. Breaking changes → `/api/v2/` (see Section 11).
- Auth required on all endpoints except `/health` and `/ready`.
- `tenant_id` always derived from JWT — never accepted as URL param or request body.
- All timestamps: ISO 8601 UTC. Never Unix timestamps in responses.
- All IDs: string prefixes (`sup_`, `pf_`, `alr_`, `imp_`) — never bare integers or UUIDs without prefix.
- Pagination: cursor-based for real-time data (alerts), offset-based for stable data (portfolio).

---

## 2. FastAPI Project Structure

Claude must follow this structure — do not invent alternatives.

```
backend/app/
├── main.py                     # FastAPI app factory, middleware registration
├── config.py                   # pydantic-settings Settings
├── dependencies.py             # Shared FastAPI dependencies (get_db, get_current_tenant, etc.)
├── middleware/
│   ├── auth.py                 # JWT validation, tenant_id extraction
│   ├── rate_limit.py           # Per-tenant rate limiting via Redis
│   └── request_id.py           # Inject X-Request-ID on every request
├── models/
│   ├── requests.py             # Pydantic request body models
│   ├── responses.py            # Pydantic response models
│   └── errors.py               # Error response models
├── repositories/
│   ├── supplier_repository.py
│   ├── score_repository.py
│   ├── alert_repository.py
│   └── portfolio_repository.py
├── services/
│   ├── portfolio_service.py    # Business logic, calls repositories
│   ├── alert_service.py
│   └── resolution_service.py
└── api/
    └── v1/
        ├── __init__.py
        └── routes/
            ├── health.py
            ├── portfolio.py
            ├── suppliers.py
            ├── alerts.py
            ├── settings.py
            └── websocket.py
```

### App Factory Pattern

```python
# backend/app/main.py
def create_app() -> FastAPI:
    app = FastAPI(
        title="Supplier Risk Intelligence Platform",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.include_router(health_router)
    app.include_router(api_v1_router, prefix="/api/v1")
    return app
```

### Dependency Injection Pattern

```python
# backend/app/dependencies.py

async def get_current_tenant(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> TenantContext:
    """Extract and validate tenant from JWT. Sets RLS context in DB."""
    payload = verify_jwt(token)                     # raises 401 if invalid
    tenant = TenantContext(
        tenant_id=payload["tenant_id"],
        user_id=payload["sub"],
        role=payload["role"],
        plan=payload["plan"],
    )
    return tenant

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session with tenant RLS context set."""
    async with async_session() as session:
        yield session

# Usage in routes — always inject, never instantiate directly
@router.get("/portfolio/suppliers")
async def list_portfolio_suppliers(
    tenant: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repository),
):
    ...
```

---

## 3. Authentication

All requests except `/health` and `/ready` must include:
```
Authorization: Bearer {jwt_token}
```

JWT issued by Auth0. Validated on every request via cached JWKS.

```python
# JWT claims structure
{
    "sub": "auth0|user_id",
    "tenant_id": "ten_01HX...",
    "role": "admin" | "viewer",
    "plan": "starter" | "growth" | "pro" | "enterprise",
    "email": "user@company.com",
    "exp": 1234567890,
    "iss": "https://{auth0_domain}/"
}
```

**JWKS caching:** Cache Auth0 public keys for 1 hour. On key miss, re-fetch before rejecting.
**Clock skew tolerance:** Accept tokens up to 30 seconds past expiry (handles clock drift).
**Role permissions:**

| Action | admin | viewer |
|---|---|---|
| Read portfolio, scores, alerts | ✅ | ✅ |
| Add/remove suppliers | ✅ | ❌ |
| Update alert status | ✅ | ✅ |
| Change alert rules / settings | ✅ | ❌ |
| Invite/remove users | ✅ | ❌ |
| Bulk import | ✅ | ❌ |

---

## 4. Standard Response Envelopes

All responses use these envelopes — no exceptions.

### Success (single object)
```json
{
  "data": { ... }
}
```

### Success (list with pagination)
```json
{
  "data": [...],
  "meta": {
    "total": 142,
    "page": 1,
    "per_page": 50,
    "total_pages": 3
  }
}
```

### Error
```json
{
  "error": {
    "code": "SUPPLIER_NOT_FOUND",
    "message": "Supplier sup_01HX... is not in your portfolio.",
    "request_id": "req_abc123def456",
    "details": {}
  }
}
```

`details` is optional — used for validation errors to list field-level problems:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "request_id": "req_abc123def456",
    "details": {
      "per_page": "Must be between 1 and 200",
      "country": "Must be a valid ISO 3166-1 alpha-2 code"
    }
  }
}
```

### Pydantic response models (use these, don't return raw dicts)

```python
# backend/app/models/responses.py

class Meta(BaseModel):
    total: int
    page: int
    per_page: int
    total_pages: int

class DataResponse(BaseModel, Generic[T]):
    data: T

class ListResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: Meta

class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, str] = {}

class ErrorResponse(BaseModel):
    error: ErrorDetail
```

---

## 5. Rate Limiting

**Implementation:** Redis sliding window counter per `tenant_id`.
**Default limit:** 1,000 requests/minute per tenant (all plans).
**Burst allowance:** 100 additional requests for up to 10 seconds.

**Rate limit headers on every response:**
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 847
X-RateLimit-Reset: 1709548800
```

**When rate limit exceeded:**
```
HTTP 429 Too Many Requests
Retry-After: 23

{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded. 1000 requests/minute allowed.",
    "request_id": "req_...",
    "details": {
      "retry_after_seconds": "23",
      "limit": "1000",
      "window": "60s"
    }
  }
}
```

**Endpoints exempt from rate limiting:**
- `GET /health`
- `GET /ready`

---

## 6. Request Validation Rules

These apply globally. Claude must enforce these in Pydantic request models.

| Parameter | Rule |
|---|---|
| `page` | integer, min 1, default 1 |
| `per_page` | integer, min 1, max 200, default 50 |
| `days` | integer, min 1, max 365, default 90 |
| `search` | string, max 200 chars, stripped of leading/trailing whitespace |
| `country` | string, exactly 2 chars, uppercase, valid ISO 3166-1 alpha-2 |
| `tags` | list of strings, max 10 items, each max 50 chars |
| `internal_id` | string, max 100 chars |
| `note` | string, max 2000 chars |
| CSV file | max 5MB, max 500 rows, must have header row |
| Webhook URL | must start with `https://` — no plain HTTP |
| Email addresses | validated format, max 10 recipients per rule |

---

## 7. Endpoints

---

### 7.1 Health & Readiness

#### `GET /health`
Liveness probe — is the process alive?
No auth required. No rate limiting.

```json
HTTP 200
{
  "status": "ok",
  "version": "1.0.0",
  "timestamp": "2025-03-04T10:00:00Z"
}
```
Always returns 200 if the process is running. Does not check dependencies.

---

#### `GET /ready`
Readiness probe — are all dependencies healthy?
No auth required. No rate limiting.

```json
HTTP 200
{
  "status": "ready",
  "dependencies": {
    "postgres": "ok",
    "redis": "ok",
    "kafka": "ok"
  },
  "timestamp": "2025-03-04T10:00:00Z"
}
```

```json
HTTP 503
{
  "status": "not_ready",
  "dependencies": {
    "postgres": "ok",
    "redis": "error: connection refused",
    "kafka": "ok"
  },
  "timestamp": "2025-03-04T10:00:00Z"
}
```

Kubernetes uses `/ready` for readiness probe, `/health` for liveness probe.

---

### 7.2 Portfolio

#### `GET /api/v1/portfolio/summary`
Dashboard overview — top-level stats for the portfolio. Called on dashboard load.
Cached in Redis: key `portfolio_summary:{tenant_id}`, TTL 5 minutes.

```json
HTTP 200
{
  "data": {
    "total_suppliers": 87,
    "high_risk_count": 12,
    "medium_risk_count": 34,
    "low_risk_count": 41,
    "unread_alerts_count": 8,
    "average_portfolio_score": 48,
    "score_trend_7d": "improving",   // "improving" | "worsening" | "stable"
    "last_scored_at": "2025-03-04T06:00:00Z",
    "plan_supplier_limit": 100,
    "plan_supplier_used": 87
  }
}
```

---

#### `GET /api/v1/portfolio/suppliers`
Paginated list of all suppliers in the tenant's portfolio.

**Query params:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `page` | int | 1 | min 1 |
| `per_page` | int | 50 | min 1, max 200 |
| `sort_by` | enum | `risk_score` | `risk_score`, `name`, `last_updated`, `date_added` |
| `sort_order` | enum | `desc` | `asc`, `desc` |
| `risk_level` | enum | — | `high`, `medium`, `low` |
| `country` | string | — | ISO 3166-1 alpha-2 |
| `search` | string | — | max 200 chars, searches canonical_name and custom_name |
| `tag` | string | — | filter by tag (exact match) |

```json
HTTP 200
{
  "data": [
    {
      "portfolio_supplier_id": "pf_01HX...",
      "supplier_id": "sup_01HX...",
      "canonical_name": "Taiwan Semiconductor Manufacturing Co",
      "custom_name": null,
      "country": "TW",
      "industry_code": "33441",
      "industry_name": "Semiconductor Manufacturing",
      "internal_id": "VEND-0042",
      "tags": ["critical", "tier-1"],
      "risk_score": 72,
      "risk_level": "high",
      "score_7d_delta": 8,
      "score_trend": "increasing",
      "unread_alerts_count": 2,
      "last_score_updated_at": "2025-03-04T06:00:00Z",
      "data_completeness": 0.91,
      "added_to_portfolio_at": "2024-09-15T00:00:00Z"
    }
  ],
  "meta": {
    "total": 87,
    "page": 1,
    "per_page": 50,
    "total_pages": 2
  }
}
```

---

#### `POST /api/v1/portfolio/suppliers`
Add a single supplier to the portfolio.

**Plan limit check:** Before adding, verify `portfolio_count < plan_supplier_limit`. Return `PLAN_LIMIT_EXCEEDED` if at limit.

**Request:**
```json
{
  "supplier_id": "sup_01HX...",
  "raw_name": "TSMC",
  "country_hint": "TW",
  "internal_id": "VEND-0042",
  "tags": ["critical", "tier-1"]
}
```
Rules: provide either `supplier_id` OR `raw_name` — not both, not neither.
If `raw_name` provided: run entity resolution inline (synchronous, < 2s).

```json
HTTP 201
{
  "data": {
    "portfolio_supplier_id": "pf_01HX...",
    "supplier_id": "sup_01HX...",
    "canonical_name": "Taiwan Semiconductor Manufacturing Co",
    "resolution_confidence": 0.97,
    "resolution_method": "fuzzy",
    "added_at": "2025-03-04T10:00:00Z"
  }
}
```

**Error cases:**
- `supplier_id` provided but not in supplier registry → `404 SUPPLIER_NOT_FOUND`
- `raw_name` provided but resolution fails → `422 RESOLUTION_FAILED`
- Supplier already in portfolio → `409 SUPPLIER_ALREADY_IN_PORTFOLIO`
- Plan limit reached → `429 PLAN_LIMIT_EXCEEDED`

---

#### `PATCH /api/v1/portfolio/suppliers/{portfolio_supplier_id}`
Update a portfolio supplier's metadata (tags, internal_id, custom_name).
Does not change the canonical supplier data.

**Request (all fields optional — send only what's changing):**
```json
{
  "custom_name": "Our TSMC Account",
  "internal_id": "VEND-0042-REV",
  "tags": ["critical", "tier-1", "semiconductor"]
}
```

```json
HTTP 200
{
  "data": {
    "portfolio_supplier_id": "pf_01HX...",
    "custom_name": "Our TSMC Account",
    "internal_id": "VEND-0042-REV",
    "tags": ["critical", "tier-1", "semiconductor"],
    "updated_at": "2025-03-04T10:05:00Z"
  }
}
```

---

#### `DELETE /api/v1/portfolio/suppliers/{portfolio_supplier_id}`
Remove supplier from portfolio. Does NOT delete the canonical supplier record or its score history.

```
HTTP 204 No Content
```

---

#### `POST /api/v1/portfolio/suppliers/import`
Bulk CSV import. Runs entity resolution asynchronously — returns immediately with import ID.

**Request:** `multipart/form-data`, field name: `file`

**CSV format:**
```csv
name,country,internal_id,tags
"Taiwan Semiconductor Manufacturing Co",TW,VEND-001,"critical;tier-1"
"Foxconn Industrial Internet Co",TW,VEND-002,"tier-1"
"Unknown Corp",,VEND-003,""
```
- `name`: required
- `country`: optional, ISO 3166-1 alpha-2, helps resolution accuracy
- `internal_id`: optional
- `tags`: optional, semicolon-separated

**Validation before accepting:**
- File must be `text/csv` or `application/csv`
- Max file size: 5MB
- Max rows: 500 (excluding header)
- Must have a header row with at minimum a `name` column
- Return `422 IMPORT_INVALID_FORMAT` immediately if any of these fail

```json
HTTP 202 Accepted
{
  "data": {
    "import_id": "imp_01HX...",
    "status": "processing",
    "total_rows": 45,
    "poll_url": "/api/v1/portfolio/imports/imp_01HX...",
    "submitted_at": "2025-03-04T10:00:00Z"
  }
}
```

---

#### `GET /api/v1/portfolio/imports/{import_id}`
Poll import job status. Poll every 2 seconds until `status` is `completed` or `failed`.

```json
HTTP 200
{
  "data": {
    "import_id": "imp_01HX...",
    "status": "completed",
    "total_rows": 45,
    "resolved_count": 42,
    "added_count": 40,
    "duplicate_count": 2,
    "unresolved_count": 3,
    "error_count": 0,
    "plan_limit_skipped_count": 0,
    "unresolved_items": [
      {
        "row": 8,
        "raw_name": "XYZ Holdings Ltd",
        "country": "DE",
        "reason": "no_match_found",
        "best_candidate": "XYZ GmbH",
        "best_confidence": 0.61
      }
    ],
    "started_at": "2025-03-04T10:00:00Z",
    "completed_at": "2025-03-04T10:00:48Z"
  }
}
```

`status` values: `processing` | `completed` | `failed`

---

### 7.3 Suppliers

#### `GET /api/v1/suppliers/{supplier_id}`
Full supplier profile with current risk score.

**Access rule:** Any authenticated tenant can view any supplier's public data and current score. Portfolio membership is NOT required to view a supplier. This enables the "search before adding" flow.

**Caching:** Redis key `supplier_profile:{supplier_id}`, TTL 1 hour. Invalidated on score update.

```json
HTTP 200
{
  "data": {
    "supplier_id": "sup_01HX...",
    "canonical_name": "Taiwan Semiconductor Manufacturing Co",
    "aliases": ["TSMC", "台積電"],
    "country": "TW",
    "industry_code": "33441",
    "industry_name": "Semiconductor Manufacturing",
    "duns_number": "123456789",
    "cik": "0001341439",
    "website": "https://www.tsmc.com",
    "primary_location": {
      "city": "Hsinchu",
      "country": "TW",
      "lat": 24.8138,
      "lng": 120.9675
    },
    "is_public_company": true,
    "in_portfolio": true,
    "portfolio_supplier_id": "pf_01HX...",
    "current_score": {
      "score": 72,
      "risk_level": "high",
      "model_version": "heuristic_v0",
      "scored_at": "2025-03-04T06:00:00Z",
      "data_completeness": 0.91,
      "signal_breakdown": {
        "financial":    { "score": 45, "weight": 0.30, "data_available": true },
        "news":         { "score": 80, "weight": 0.25, "data_available": true },
        "shipping":     { "score": 60, "weight": 0.20, "data_available": true },
        "geopolitical": { "score": 90, "weight": 0.15, "data_available": true },
        "macro":        { "score": 55, "weight": 0.10, "data_available": true }
      },
      "top_drivers": [
        {
          "signal_name": "country_risk_score",
          "display_name": "Taiwan geopolitical risk",
          "category": "geopolitical",
          "contribution": 18,
          "direction": "increases_risk",
          "raw_value": 85.0,
          "explanation": "Country political stability index is 85/100 (elevated). Cross-strait tensions are the primary driver."
        },
        {
          "signal_name": "news_negative_count_30d",
          "display_name": "Negative news volume (30 days)",
          "category": "news",
          "contribution": 12,
          "direction": "increases_risk",
          "raw_value": 7.0,
          "explanation": "7 negative news articles in the last 30 days (threshold: 5)."
        }
      ]
    }
  }
}
```

---

#### `GET /api/v1/suppliers/{supplier_id}/score-history`
Historical scores for chart rendering. Returns one data point per scored day.

**Query params:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `days` | int | 90 | min 1, max 365 |

```json
HTTP 200
{
  "data": {
    "supplier_id": "sup_01HX...",
    "days_requested": 90,
    "days_available": 67,
    "scores": [
      {
        "date": "2025-01-01",
        "score": 55,
        "risk_level": "medium",
        "model_version": "heuristic_v0"
      },
      {
        "date": "2025-01-02",
        "score": 57,
        "risk_level": "medium",
        "model_version": "heuristic_v0"
      }
    ]
  }
}
```

Note: `days_available` may be less than `days_requested` if the supplier was added recently or data is sparse.

---

#### `GET /api/v1/suppliers/{supplier_id}/news`
Recent news articles that contributed to this supplier's score.

**Query params:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `page` | int | 1 | min 1 |
| `per_page` | int | 20 | min 1, max 100 |
| `sentiment` | enum | — | `positive`, `negative`, `neutral` |
| `days` | int | 30 | min 1, max 90 |

```json
HTTP 200
{
  "data": [
    {
      "article_id": "a3f9b2...",
      "title": "TSMC faces power shortage risk amid Taiwan drought",
      "url": "https://reuters.com/article/...",
      "source_name": "Reuters",
      "source_credibility": 1.0,
      "published_at": "2025-03-03T14:00:00Z",
      "sentiment_score": -0.72,
      "sentiment_label": "negative",
      "sentiment_model": "finbert",
      "topics": ["regulatory", "disaster"],
      "score_contribution": 4,
      "content_available": true
    }
  ],
  "meta": {
    "total": 14,
    "page": 1,
    "per_page": 20,
    "total_pages": 1
  }
}
```

---

#### `POST /api/v1/suppliers/resolve`
Resolve a raw company name to a canonical supplier. Used before adding to portfolio.

**Request:**
```json
{
  "name": "TSMC",
  "country_hint": "TW",
  "context": "Major semiconductor manufacturer based in Taiwan"
}
```

`context` is optional — passed to LLM stage if fuzzy match is inconclusive.

```json
HTTP 200
{
  "data": {
    "resolved": true,
    "supplier_id": "sup_01HX...",
    "canonical_name": "Taiwan Semiconductor Manufacturing Co",
    "country": "TW",
    "confidence": 0.97,
    "match_method": "fuzzy",
    "alternatives": []
  }
}
```

When unresolved:
```json
HTTP 200
{
  "data": {
    "resolved": false,
    "supplier_id": null,
    "canonical_name": null,
    "confidence": 0.0,
    "match_method": "unresolved",
    "alternatives": [
      {
        "supplier_id": "sup_02HX...",
        "canonical_name": "XYZ Holdings Ltd",
        "country": "DE",
        "confidence": 0.61
      }
    ]
  }
}
```

Returns `200` even when unresolved — `resolved: false` is a valid outcome, not an error.

---

### 7.4 Alerts

#### `GET /api/v1/alerts`
All alerts for the tenant, newest first.

**Query params:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `status` | enum | `new` | `new`, `investigating`, `resolved`, `dismissed`, `all` |
| `severity` | enum | — | `low`, `medium`, `high`, `critical` |
| `supplier_id` | string | — | filter to one supplier |
| `alert_type` | enum | — | `score_spike`, `high_threshold`, `event_detected`, `sanctions_hit` |
| `page` | int | 1 | min 1 |
| `per_page` | int | 50 | min 1, max 200 |

```json
HTTP 200
{
  "data": [
    {
      "alert_id": "alr_01HX...",
      "supplier_id": "sup_01HX...",
      "supplier_name": "Taiwan Semiconductor Manufacturing Co",
      "alert_type": "score_spike",
      "severity": "high",
      "title": "Risk score rose 18 points in 7 days",
      "message": "Score increased from 54 to 72. Primary driver: 7 negative news articles and elevated geopolitical risk.",
      "metadata": {
        "score_before": 54,
        "score_after": 72,
        "score_delta": 18,
        "period_days": 7,
        "top_signal": "news_negative_count_30d"
      },
      "status": "new",
      "note": null,
      "fired_at": "2025-03-04T06:05:00Z",
      "read_at": null,
      "resolved_at": null
    }
  ],
  "meta": {
    "total": 23,
    "page": 1,
    "per_page": 50,
    "total_pages": 1
  }
}
```

---

#### `PATCH /api/v1/alerts/{alert_id}`
Update alert status and/or add a note.

**Valid state transitions:**

```
new → investigating   ✅
new → resolved        ✅
new → dismissed       ✅
investigating → resolved  ✅
investigating → new   ✅  (re-open)
resolved → investigating  ✅  (re-open)
dismissed → investigating ✅  (re-open)
dismissed → new       ❌  (use investigating instead)
resolved → dismissed  ❌  (already closed)
```

**Request (all fields optional):**
```json
{
  "status": "investigating",
  "note": "Spoke with supplier contact — they report normal operations. Monitoring for 2 weeks."
}
```

```json
HTTP 200
{
  "data": {
    "alert_id": "alr_01HX...",
    "status": "investigating",
    "note": "Spoke with supplier contact — they report normal operations. Monitoring for 2 weeks.",
    "updated_at": "2025-03-04T11:00:00Z"
  }
}
```

**Error cases:**
- Invalid state transition → `422 INVALID_STATE_TRANSITION` with `details.allowed_transitions`
- Alert not in tenant's portfolio → `404 ALERT_NOT_FOUND`
- `viewer` role attempting status change → `403 FORBIDDEN` (viewers can read only)

---

### 7.5 Settings

#### `GET /api/v1/settings/alert-rules`
Get the tenant's alert configuration.

```json
HTTP 200
{
  "data": {
    "score_spike_threshold": 15,
    "high_risk_threshold": 70,
    "channels": {
      "email": {
        "enabled": true,
        "recipients": ["maya@company.com", "rohan@company.com"]
      },
      "slack": {
        "enabled": false,
        "webhook_url": null,
        "webhook_verified": false
      },
      "webhook": {
        "enabled": false,
        "url": null,
        "secret": null
      }
    },
    "updated_at": "2025-03-01T09:00:00Z"
  }
}
```

---

#### `PUT /api/v1/settings/alert-rules`
Replace alert configuration entirely. Admin only.

**Request:**
```json
{
  "score_spike_threshold": 15,
  "high_risk_threshold": 70,
  "channels": {
    "email": {
      "enabled": true,
      "recipients": ["maya@company.com"]
    },
    "slack": {
      "enabled": true,
      "webhook_url": "https://hooks.slack.com/services/T00/B00/xxx"
    },
    "webhook": {
      "enabled": false
    }
  }
}
```

**Validation:**
- `score_spike_threshold`: int, min 5, max 50
- `high_risk_threshold`: int, min 50, max 95
- `recipients`: list, max 10 items, each a valid email
- `webhook_url`: must start with `https://`
- Slack webhook URL: verified by sending a test message before saving — returns `422 SLACK_WEBHOOK_INVALID` if test fails

```json
HTTP 200
{
  "data": {
    "score_spike_threshold": 15,
    "high_risk_threshold": 70,
    "channels": { ... },
    "slack_verified": true,
    "updated_at": "2025-03-04T11:00:00Z"
  }
}
```

---

#### `GET /api/v1/settings/users`
List users in the tenant. Admin only.

```json
HTTP 200
{
  "data": [
    {
      "user_id": "usr_01HX...",
      "email": "maya@company.com",
      "role": "admin",
      "created_at": "2024-09-01T00:00:00Z",
      "last_active_at": "2025-03-04T08:30:00Z"
    }
  ],
  "meta": { "total": 3, "page": 1, "per_page": 50, "total_pages": 1 }
}
```

---

#### `POST /api/v1/settings/users/invite`
Invite a new user. Sends email via SendGrid. Admin only.

**Request:**
```json
{
  "email": "newuser@company.com",
  "role": "viewer"
}
```

```json
HTTP 201
{
  "data": {
    "invite_id": "inv_01HX...",
    "email": "newuser@company.com",
    "role": "viewer",
    "expires_at": "2025-03-11T10:00:00Z"
  }
}
```

**Error cases:**
- User already exists in tenant → `409 USER_ALREADY_EXISTS`
- Plan user limit exceeded → `429 PLAN_LIMIT_EXCEEDED`

---

#### `DELETE /api/v1/settings/users/{user_id}`
Remove a user from the tenant. Admin only. Cannot remove yourself.

```
HTTP 204 No Content
```

---

### 7.6 Risk Map

#### `GET /api/v1/map/suppliers`
GeoJSON for risk map. Returns all suppliers in the tenant's portfolio that have location data.

```json
HTTP 200
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [120.9675, 24.8138]
      },
      "properties": {
        "supplier_id": "sup_01HX...",
        "canonical_name": "Taiwan Semiconductor Manufacturing Co",
        "risk_score": 72,
        "risk_level": "high",
        "country": "TW",
        "unread_alerts_count": 2
      }
    }
  ],
  "meta": {
    "total_suppliers": 87,
    "suppliers_with_location": 79,
    "generated_at": "2025-03-04T10:00:00Z"
  }
}
```

Cached: Redis key `map_geojson:{tenant_id}`, TTL 10 minutes.

---

## 8. WebSocket: Real-time Alerts

```
wss://api.yourdomain.com/ws/alerts
```

### Connection

```
GET /ws/alerts
Upgrade: websocket
Connection: Upgrade
Authorization: Bearer {jwt_token}
```

Auth failure during handshake → HTTP 401, connection rejected.

### Message Types

**Server → Client: alert fired**
```json
{
  "type": "alert.fired",
  "data": {
    "alert_id": "alr_01HX...",
    "supplier_id": "sup_01HX...",
    "supplier_name": "Taiwan Semiconductor Manufacturing Co",
    "alert_type": "score_spike",
    "severity": "high",
    "title": "Risk score rose 18 points",
    "fired_at": "2025-03-04T06:05:00Z"
  }
}
```

**Server → Client: score updated**
```json
{
  "type": "score.updated",
  "data": {
    "supplier_id": "sup_01HX...",
    "new_score": 72,
    "previous_score": 54,
    "risk_level": "high",
    "scored_at": "2025-03-04T06:00:00Z"
  }
}
```

**Server → Client: heartbeat (every 30 seconds)**
```json
{ "type": "ping", "timestamp": "2025-03-04T10:00:30Z" }
```

**Client → Server: heartbeat acknowledgement**
```json
{ "type": "pong" }
```

### Reconnection Behaviour

Client must implement exponential backoff reconnection:
- Attempt 1: wait 1s
- Attempt 2: wait 2s
- Attempt 3: wait 4s
- Attempts 4+: wait 30s (max)

Server does not buffer messages during disconnection. Client must call `GET /api/v1/alerts` on reconnect to catch any missed alerts.

### Connection Limits

- Max 5 concurrent WebSocket connections per tenant
- Connection times out after 5 minutes of no `pong` response
- JWT expiry during connection: server sends `{"type": "auth.expired"}` and closes connection

---

## 9. Plan Limits Enforcement

Check plan limits before write operations. Return `429 PLAN_LIMIT_EXCEEDED` if exceeded.

| Plan | Max Suppliers | Max Users | Checks enforced at |
|---|---|---|---|
| starter | 25 | 3 | POST /portfolio/suppliers, POST /import, POST /users/invite |
| growth | 100 | 10 | same |
| pro | 500 | unlimited | same |
| enterprise | unlimited | unlimited | never |

**Response when limit exceeded:**
```json
HTTP 429
{
  "error": {
    "code": "PLAN_LIMIT_EXCEEDED",
    "message": "Your Starter plan allows 25 suppliers. You have 25. Upgrade to Growth for up to 100 suppliers.",
    "request_id": "req_...",
    "details": {
      "current_count": "25",
      "plan_limit": "25",
      "current_plan": "starter",
      "upgrade_url": "https://app.yourdomain.com/settings/billing"
    }
  }
}
```

---

## 10. Error Codes (Complete)

| Code | HTTP Status | Meaning | When |
|---|---|---|---|
| `UNAUTHORIZED` | 401 | Missing, invalid, or expired JWT | Any protected endpoint |
| `FORBIDDEN` | 403 | Valid JWT but insufficient role | Viewer attempting write |
| `NOT_FOUND` | 404 | Generic resource not found | — |
| `SUPPLIER_NOT_FOUND` | 404 | Supplier not found | GET /suppliers/{id} |
| `ALERT_NOT_FOUND` | 404 | Alert not in tenant portfolio | PATCH /alerts/{id} |
| `IMPORT_NOT_FOUND` | 404 | Import job not found | GET /imports/{id} |
| `SUPPLIER_ALREADY_IN_PORTFOLIO` | 409 | Duplicate add | POST /portfolio/suppliers |
| `USER_ALREADY_EXISTS` | 409 | User already in tenant | POST /users/invite |
| `VALIDATION_ERROR` | 422 | Request body/param validation failed | Any endpoint |
| `RESOLUTION_FAILED` | 422 | Company name could not be resolved | POST /suppliers/resolve, POST /portfolio/suppliers |
| `IMPORT_INVALID_FORMAT` | 422 | CSV missing header, too large, wrong format | POST /import |
| `INVALID_STATE_TRANSITION` | 422 | Alert status transition not allowed | PATCH /alerts/{id} |
| `SLACK_WEBHOOK_INVALID` | 422 | Slack webhook URL failed verification | PUT /settings/alert-rules |
| `PLAN_LIMIT_EXCEEDED` | 429 | Supplier or user count at plan limit | POST /portfolio/suppliers, POST /users/invite |
| `RATE_LIMITED` | 429 | Too many requests | Any endpoint |
| `INTERNAL_ERROR` | 500 | Unhandled server error — check Sentry | Any endpoint |

---

## 11. API Versioning Strategy

**Current version:** v1 (`/api/v1/`)

**When to create v2:**
- Removing a field from a response (breaking)
- Renaming a field (breaking)
- Changing a field's type (breaking)
- Removing an endpoint (breaking)

**Non-breaking changes (do NOT require v2):**
- Adding a new optional field to a response
- Adding a new endpoint
- Adding a new optional query parameter
- Making a previously required field optional

**v1 → v2 migration process:**
1. Create `/api/v2/` router with new behaviour
2. Keep `/api/v1/` running unchanged
3. Add `Deprecation` header to v1 responses: `Deprecation: true`
4. Add `Sunset` header with removal date: `Sunset: Sat, 01 Jan 2027 00:00:00 GMT`
5. Notify all tenants via email 90 days before v1 sunset
6. Remove v1 only after sunset date

---

## 12. Caching Summary

| Endpoint | Cache Key | TTL | Invalidated When |
|---|---|---|---|
| `GET /portfolio/summary` | `portfolio_summary:{tenant_id}` | 5 min | Supplier added/removed, alert read |
| `GET /portfolio/suppliers` | Not cached (paginated, filtered) | — | — |
| `GET /suppliers/{id}` | `supplier_profile:{supplier_id}` | 1 hour | Score updated |
| `GET /suppliers/{id}/score-history` | `score_history:{supplier_id}:{days}` | 6 hours | Score updated |
| `GET /suppliers/{id}/news` | Not cached (too dynamic) | — | — |
| `GET /alerts` | Not cached | — | — |
| `GET /map/suppliers` | `map_geojson:{tenant_id}` | 10 min | Supplier added/removed |

---

*See PRODUCT_SPEC.md for feature-level acceptance criteria and user journeys.*
*See ARCHITECTURE.md Section 3 for how backend serves scores from the warm Postgres copy.*
