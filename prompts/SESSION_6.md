# SESSION_6.md — FastAPI Backend: Core API
## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `specs/API_SPEC.md` — full endpoint contracts
3. `docs/ARCHITECTURE.md` — Section 3 (data flow), Section 4 (Postgres schema)

Confirm:
> "I am building the FastAPI backend. It reads scores from Postgres and serves them via REST API. Auth is JWT from Auth0. All DB access goes through repository classes. No raw SQL in routes."

---

## RULES FOR THIS SESSION

- Read `specs/API_SPEC.md` Section 2 before writing a single route — follow the project structure exactly.
- Every route uses `Depends()` for auth and repositories — never instantiate directly in route handlers.
- All request bodies are Pydantic v2 models. All responses use the envelope format from API_SPEC.md Section 4.
- Run `make lint` after Steps 2 and 4. Run `make test` after Step 5.
- Do not build WebSocket, alert engine, or Celery in this session — those are Session 7.
- Do not build the React frontend — that is Session 8.

---

## STEP 1: Postgres Schema Migration

Create `backend/app/db/migrations/001_initial_schema.sql` — the full app schema.

Tables to create (from ARCHITECTURE.md Section 4):
- `tenants` — id, name, plan, stripe_customer_id, max_suppliers, created_at
- `users` — id, tenant_id, email, role, auth0_id, created_at
- `suppliers` — id (sup_ prefix), canonical_name, aliases TEXT[], country, industry_code, duns_number, cik, website, is_public_company, created_at
- `portfolio_suppliers` — id (pf_ prefix), tenant_id, supplier_id, custom_name, internal_id, tags TEXT[], added_at
- `supplier_scores` — supplier_id, score, risk_level, score_date, signal_breakdown JSONB, model_version, data_completeness, scored_at. UNIQUE on (supplier_id, score_date).
- `alerts` — id (alr_ prefix), tenant_id, supplier_id, alert_type, severity, title, message, metadata JSONB, status, note, fired_at, read_at, resolved_at
- `alert_rules` — tenant_id PRIMARY KEY, score_spike_threshold, high_risk_threshold, channels JSONB, updated_at
- `disruption_reports` — id, supplier_id, tenant_id, disruption_type, disruption_date, severity, source, confidence, created_at

Rules:
- All timestamps: `TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- Use `gen_random_uuid()` for UUID defaults
- Add indexes: `supplier_scores(supplier_id, score_date DESC)`, `alerts(tenant_id, status)`, `portfolio_suppliers(tenant_id)`
- Add Row Level Security policies on `portfolio_suppliers` and `alerts` (tenant_id must match)

Also create `backend/app/db/__init__.py` and `backend/app/db/connection.py`:
```python
# connection.py — asyncpg pool factory
async def create_pool(settings: Settings) -> asyncpg.Pool: ...
async def get_pool() -> asyncpg.Pool: ...  # FastAPI dependency
```

**When Step 1 is done, say: "✅ Step 1 complete."**

---

## STEP 2: Request/Response Models + Dependencies

### `backend/app/models/requests.py`
Pydantic v2 request models for every endpoint. Enforce all validation rules from API_SPEC.md Section 6:
- `per_page`: int, min 1, max 200, default 50
- `page`: int, min 1, default 1
- `days`: int, min 1, max 365, default 90
- `search`: str, max 200 chars, stripped
- `country`: str, exactly 2 chars, uppercase
- `tags`: list[str], max 10 items, each max 50 chars

Key request models:
```python
class PortfolioSuppliersParams(BaseModel): ...     # GET /portfolio/suppliers query params
class AddSupplierRequest(BaseModel): ...            # POST /portfolio/suppliers
class PatchPortfolioSupplierRequest(BaseModel): ... # PATCH /portfolio/suppliers/{id}
class PatchAlertRequest(BaseModel): ...             # PATCH /alerts/{id}
class AlertRulesRequest(BaseModel): ...             # PUT /settings/alert-rules
class InviteUserRequest(BaseModel): ...             # POST /settings/users/invite
class ResolveSupplierRequest(BaseModel): ...        # POST /suppliers/resolve
```

### `backend/app/models/responses.py`
Response models and envelopes from API_SPEC.md Section 4:
```python
class Meta(BaseModel): ...
class DataResponse(BaseModel, Generic[T]): ...
class ListResponse(BaseModel, Generic[T]): ...
class ErrorDetail(BaseModel): ...
class ErrorResponse(BaseModel): ...
```

Plus entity-specific response models:
```python
class SupplierSummary(BaseModel): ...        # row in portfolio list
class SupplierProfile(BaseModel): ...        # full profile with score
class ScoreHistory(BaseModel): ...           # for score history endpoint
class AlertResponse(BaseModel): ...          # single alert
class PortfolioSummaryResponse(BaseModel):...# dashboard stats
```

### `backend/app/dependencies.py`
FastAPI dependencies (inject these everywhere, never instantiate directly):
```python
async def get_current_tenant(token: str = Depends(oauth2_scheme)) -> TenantContext: ...
async def get_db_pool() -> asyncpg.Pool: ...
async def require_admin(tenant: TenantContext = Depends(get_current_tenant)) -> TenantContext: ...
```

`TenantContext` Pydantic model:
```python
class TenantContext(BaseModel):
    tenant_id: str
    user_id: str
    role: Literal["admin", "viewer"]
    plan: Literal["starter", "growth", "pro", "enterprise"]
    email: str
```

JWT validation: use `python-jose` to verify Auth0 JWT. Cache JWKS for 1 hour.
In tests: `mock_tenant` fixture in `conftest.py` returns a `TenantContext` without hitting Auth0.

**Run `make lint` — fix all errors before proceeding.**
**Say: "✅ Step 2 complete — lint passing."**

---

## STEP 3: Repositories

Create one repository per major entity. Follow the Protocol + InMemory + Postgres pattern (ADR-010).

### `backend/app/repositories/supplier_repository.py`
```python
class SupplierRepository(Protocol):
    async def get_by_id(self, supplier_id: str) -> SupplierProfile | None: ...
    async def get_portfolio_suppliers(
        self, tenant_id: str, params: PortfolioSuppliersParams
    ) -> tuple[list[SupplierSummary], int]: ...  # (results, total_count)
    async def add_to_portfolio(
        self, tenant_id: str, request: AddSupplierRequest
    ) -> PortfolioSupplierRecord: ...
    async def remove_from_portfolio(
        self, tenant_id: str, portfolio_supplier_id: str
    ) -> None: ...
    async def patch_portfolio_supplier(
        self, tenant_id: str, portfolio_supplier_id: str,
        request: PatchPortfolioSupplierRequest
    ) -> PortfolioSupplierRecord: ...
    async def count_portfolio(self, tenant_id: str) -> int: ...

class InMemorySupplierRepository: ...   # for tests
class PostgresSupplierRepository: ...   # production
```

### `backend/app/repositories/score_repository.py`
```python
class ScoreRepository(Protocol):
    async def get_latest_score(self, supplier_id: str) -> SupplierScore | None: ...
    async def get_score_history(
        self, supplier_id: str, days: int
    ) -> list[SupplierScore]: ...
    async def get_portfolio_summary(self, tenant_id: str) -> PortfolioSummaryData: ...

class InMemoryScoreRepository: ...
class PostgresScoreRepository: ...
```

### `backend/app/repositories/alert_repository.py`
```python
class AlertRepository(Protocol):
    async def list_alerts(
        self, tenant_id: str, status: str | None, severity: str | None,
        supplier_id: str | None, page: int, per_page: int
    ) -> tuple[list[AlertResponse], int]: ...
    async def patch_alert(
        self, tenant_id: str, alert_id: str, request: PatchAlertRequest
    ) -> AlertResponse: ...

class InMemoryAlertRepository: ...
class PostgresAlertRepository: ...
```

Note: Alert state transition validation belongs in the repository, not the route handler.
Valid transitions are defined in API_SPEC.md Section 7.4. Return `InvalidStateTransitionError`
for invalid transitions — the route handler converts this to a 422 response.

**Say: "✅ Step 3 complete."**

---

## STEP 4: Routes

Create routes following API_SPEC.md Section 7 exactly.

### `backend/app/api/v1/routes/health.py`
- `GET /health` — liveness, no auth, always 200
- `GET /ready` — readiness, checks Postgres + Redis + Kafka connections

### `backend/app/api/v1/routes/portfolio.py`
- `GET /api/v1/portfolio/summary` — cached in Redis 5 min, key `portfolio_summary:{tenant_id}`
- `GET /api/v1/portfolio/suppliers` — paginated, filtered
- `POST /api/v1/portfolio/suppliers` — add single supplier, plan limit check first
- `PATCH /api/v1/portfolio/suppliers/{portfolio_supplier_id}`
- `DELETE /api/v1/portfolio/suppliers/{portfolio_supplier_id}` → 204
- `POST /api/v1/portfolio/suppliers/import` → 202, async job
- `GET /api/v1/portfolio/imports/{import_id}` — poll job status

### `backend/app/api/v1/routes/suppliers.py`
- `GET /api/v1/suppliers/{supplier_id}` — cached in Redis 1h, key `supplier_profile:{supplier_id}`
- `GET /api/v1/suppliers/{supplier_id}/score-history`
- `GET /api/v1/suppliers/{supplier_id}/news`
- `POST /api/v1/suppliers/resolve` — entity resolution

### `backend/app/api/v1/routes/alerts.py`
- `GET /api/v1/alerts` — list with filters
- `PATCH /api/v1/alerts/{alert_id}` — status + note update

### `backend/app/api/v1/routes/settings.py`
- `GET /api/v1/settings/alert-rules`
- `PUT /api/v1/settings/alert-rules` — admin only
- `GET /api/v1/settings/users` — admin only
- `POST /api/v1/settings/users/invite` — admin only
- `DELETE /api/v1/settings/users/{user_id}` — admin only, cannot delete self

### Plan limit enforcement
Implement `check_plan_limit()` as a reusable function in `backend/app/services/plan_limits.py`:
```python
PLAN_LIMITS = {
    "starter":    {"suppliers": 25,  "users": 3},
    "growth":     {"suppliers": 100, "users": 10},
    "pro":        {"suppliers": 500, "users": None},
    "enterprise": {"suppliers": None, "users": None},
}

async def check_supplier_limit(tenant: TenantContext, repo: SupplierRepository) -> None:
    """Raises PlanLimitExceededError if at limit. Call before every add operation."""
    ...
```

### Error handling
Create `backend/app/middleware/error_handler.py` — register exception handlers on the app:
- `PlanLimitExceededError` → 429 with `PLAN_LIMIT_EXCEEDED` code
- `SupplierNotFoundError` → 404 with `SUPPLIER_NOT_FOUND` code
- `InvalidStateTransitionError` → 422 with `INVALID_STATE_TRANSITION` code + allowed_transitions in details
- `ForbiddenError` → 403 with `FORBIDDEN` code
- `ValidationError` (Pydantic) → 422 with `VALIDATION_ERROR` code + field details
- Unhandled `Exception` → 500 with `INTERNAL_ERROR` code, log to Sentry

All error responses use the envelope from API_SPEC.md Section 4. Every response includes `request_id` from the `X-Request-ID` middleware.

### Rate limiting
Create `backend/app/middleware/rate_limit.py`:
- Redis sliding window: key `rate_limit:{tenant_id}`, 1000 req/min
- Add `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers on every response
- Return 429 with `RATE_LIMITED` code when exceeded
- Exempt `/health` and `/ready`

**Run `make lint` — fix all errors before proceeding.**
**Say: "✅ Step 4 complete — lint passing."**

---

## STEP 5: Tests

### `tests/backend/test_health.py`
- `GET /health` → 200, no auth required
- `GET /ready` → 200 when all deps up, 503 when Postgres down (mock pool failure)

### `tests/backend/test_portfolio.py`
- `GET /portfolio/summary` → correct structure, uses mock tenant
- `GET /portfolio/suppliers` → paginated response, filters work
- `POST /portfolio/suppliers` → 201, supplier added
- `POST /portfolio/suppliers` → 409 when already in portfolio
- `POST /portfolio/suppliers` → 429 when plan limit reached
- `DELETE /portfolio/suppliers/{id}` → 204
- `PATCH /portfolio/suppliers/{id}` → 200, fields updated

### `tests/backend/test_suppliers.py`
- `GET /suppliers/{id}` → 200, correct profile structure
- `GET /suppliers/{id}` → 404 when not found
- `GET /suppliers/{id}/score-history` → 200, correct structure
- `POST /suppliers/resolve` → 200 with resolved=true
- `POST /suppliers/resolve` → 200 with resolved=false (valid outcome)

### `tests/backend/test_alerts.py`
- `GET /alerts` → paginated list
- `GET /alerts?status=new` → filtered list
- `PATCH /alerts/{id}` → valid state transition succeeds
- `PATCH /alerts/{id}` → 422 on invalid state transition with allowed_transitions in details
- `PATCH /alerts/{id}` → 403 when viewer tries to change status (read-only)

### `tests/backend/test_settings.py`
- `GET /settings/alert-rules` → 200
- `PUT /settings/alert-rules` → 200, rules updated
- `PUT /settings/alert-rules` → 403 when viewer role
- `POST /settings/users/invite` → 201
- `DELETE /settings/users/{id}` → 403 when trying to delete self

### `tests/backend/test_middleware.py`
- Rate limit: 1001st request in 1 minute → 429
- Rate limit headers present on every response
- `X-Request-ID` header present on every response
- Unhandled exception → 500 with `INTERNAL_ERROR` code (not stack trace)

**Run `make test` — must pass with ≥ 80% coverage on `backend/`.**
**Say: "✅ Step 5 complete — X tests passing, Y% coverage."**

---

## STEP 6: Wire Up `main.py`

Complete `backend/app/main.py` — the app factory:

```python
def create_app() -> FastAPI:
    app = FastAPI(
        title="Supplier Risk Intelligence Platform",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    # Middleware (order matters — outermost first)
    app.add_middleware(RequestIDMiddleware)   # sets X-Request-ID
    app.add_middleware(RateLimitMiddleware)   # rate limiting
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins)

    # Exception handlers
    register_exception_handlers(app)

    # Startup/shutdown
    @app.on_event("startup")
    async def startup(): ...   # create asyncpg pool, warm Redis connection

    @app.on_event("shutdown")
    async def shutdown(): ...  # close pool

    # Routers
    app.include_router(health_router)
    app.include_router(api_v1_router, prefix="/api/v1")

    return app

app = create_app()
```

Verify it starts:
```bash
conda run -n genai uvicorn backend.app.main:app --reload --port 8000
```
Hit `http://localhost:8000/health` — should return `{"status": "ok"}`.

**Say: "✅ Step 6 complete — server starts, /health returns 200."**

---

## SESSION 6 DONE — CHECKLIST

```
□ make lint passes clean — zero ruff and mypy errors
□ make test passes — ≥ 80% coverage on backend/
□ GET /health returns 200 with no auth
□ GET /ready checks Postgres, Redis, Kafka dependencies
□ All routes follow API_SPEC.md envelope format exactly
□ JWT auth validated on all routes except /health and /ready
□ tenant_id always from JWT — never accepted as URL param or body
□ Repository pattern: Protocol + InMemory + Postgres for every entity
□ Plan limits enforced before every add operation (suppliers + users)
□ Alert state transitions validated — invalid transitions → 422
□ Rate limiting: 1000 req/min, X-RateLimit-* headers on every response
□ X-Request-ID header on every response
□ All errors use the standard envelope from API_SPEC.md Section 4
□ No raw SQL in route handlers — all through repositories
□ No Pydantic v1 syntax anywhere
□ Server starts with uvicorn and /health returns 200
```

**Say: "Session 6 complete. Checklist: X/16 items green."**

If any item is red — fix it before declaring done.

---

## WHAT COMES NEXT

Session 7: Alert engine, Celery task dispatch, WebSocket real-time alerts.
Session 8: React frontend — portfolio dashboard, supplier profile, alert centre.

Commit before starting Session 7:
```
git add .
git commit -m "feat(session-6): FastAPI backend, all REST endpoints, auth, rate limiting"
git push origin main
```
