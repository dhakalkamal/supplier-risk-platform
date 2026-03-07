"""Portfolio routes — supplier list, add, patch, delete, and bulk import.

GET    /api/v1/portfolio/summary
GET    /api/v1/portfolio/suppliers
POST   /api/v1/portfolio/suppliers
POST   /api/v1/portfolio/suppliers/import     ← declared before /{id} to avoid conflict
PATCH  /api/v1/portfolio/suppliers/{portfolio_supplier_id}
DELETE /api/v1/portfolio/suppliers/{portfolio_supplier_id}
GET    /api/v1/portfolio/imports/{import_id}
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request, UploadFile
from fastapi.responses import Response

from backend.app.dependencies import (
    TenantContext,
    get_current_tenant,
    get_score_repository,
    get_supplier_repository,
)
from backend.app.models.requests import (
    AddSupplierRequest,
    PatchPortfolioSupplierRequest,
    PortfolioSuppliersParams,
)
from backend.app.models.responses import (
    AddSupplierResponse,
    DataResponse,
    ImportJobResponse,
    ImportStatusResponse,
    ListResponse,
    Meta,
    PatchPortfolioSupplierResponse,
    PortfolioSummaryResponse,
    SupplierSummary,
)
from backend.app.repositories.score_repository import ScoreRepository
from backend.app.repositories.supplier_repository import SupplierRepository
from backend.app.services.plan_limits import PLAN_LIMITS, check_supplier_limit

log = structlog.get_logger()
router = APIRouter(prefix="/portfolio", tags=["portfolio"])

_SUMMARY_CACHE_TTL = 300  # 5 minutes
_IMPORT_KEY_TTL = 3600  # 1 hour


@router.get("/summary", response_model=DataResponse[PortfolioSummaryResponse])
async def get_portfolio_summary(
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
    score_repo: ScoreRepository = Depends(get_score_repository),
) -> DataResponse[PortfolioSummaryResponse]:
    """Aggregate portfolio stats. Cached in Redis for 5 minutes."""
    redis = _get_redis(request)
    cache_key = f"portfolio_summary:{tenant.tenant_id}"

    if redis is not None:
        cached = await redis.get(cache_key)
        if cached:
            data = PortfolioSummaryResponse.model_validate_json(cached)
            return DataResponse(data=data)

    plan_limit = PLAN_LIMITS[tenant.plan]["suppliers"]
    raw = await score_repo.get_portfolio_summary(tenant.tenant_id, plan_limit)
    response_data = PortfolioSummaryResponse(
        total_suppliers=raw.total_suppliers,
        high_risk_count=raw.high_risk_count,
        medium_risk_count=raw.medium_risk_count,
        low_risk_count=raw.low_risk_count,
        unread_alerts_count=raw.unread_alerts_count,
        average_portfolio_score=raw.average_portfolio_score,
        score_trend_7d=raw.score_trend_7d,  # type: ignore[arg-type]
        last_scored_at=raw.last_scored_at,
        plan_supplier_limit=plan_limit,
        plan_supplier_used=raw.plan_supplier_used,
    )

    if redis is not None:
        await redis.setex(cache_key, _SUMMARY_CACHE_TTL, response_data.model_dump_json())

    return DataResponse(data=response_data)


@router.get("/suppliers", response_model=ListResponse[SupplierSummary])
async def list_portfolio_suppliers(
    params: PortfolioSuppliersParams = Depends(),
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
) -> ListResponse[SupplierSummary]:
    """Paginated, filterable list of suppliers in the tenant's portfolio."""
    items, total = await supplier_repo.get_portfolio_suppliers(tenant.tenant_id, params)
    total_pages = max(1, (total + params.per_page - 1) // params.per_page)
    return ListResponse(
        data=items,
        meta=Meta(
            total=total,
            page=params.page,
            per_page=params.per_page,
            total_pages=total_pages,
        ),
    )


@router.post(
    "/suppliers",
    response_model=DataResponse[AddSupplierResponse],
    status_code=201,
)
async def add_supplier(
    body: AddSupplierRequest,
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
) -> DataResponse[AddSupplierResponse]:
    """Add a supplier to the portfolio. Raises 429 when plan limit is reached."""
    await check_supplier_limit(tenant, supplier_repo)
    record = await supplier_repo.add_to_portfolio(tenant.tenant_id, body)
    return DataResponse(
        data=AddSupplierResponse(
            portfolio_supplier_id=record.portfolio_supplier_id,
            supplier_id=record.supplier_id,
            canonical_name=record.canonical_name,
            resolution_confidence=record.resolution_confidence,
            resolution_method=record.resolution_method,
            added_at=record.added_at,
        )
    )


@router.post(
    "/suppliers/import",
    response_model=DataResponse[ImportJobResponse],
    status_code=202,
)
async def import_suppliers(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
) -> DataResponse[ImportJobResponse]:
    """Accept a CSV upload and process rows asynchronously.

    CSV must have a 'name' column. Optional columns: country, internal_id, tags.
    Poll status at GET /api/v1/portfolio/imports/{import_id}.
    """
    content = await file.read()
    rows = _parse_csv(content)
    import_id = "imp_" + uuid.uuid4().hex
    now = datetime.now(tz=timezone.utc)

    redis = _get_redis(request)
    initial: dict[str, Any] = {
        "import_id": import_id,
        "status": "processing",
        "total_rows": len(rows),
        "resolved_count": 0,
        "added_count": 0,
        "duplicate_count": 0,
        "unresolved_count": 0,
        "error_count": 0,
        "plan_limit_skipped_count": 0,
        "unresolved_items": [],
        "started_at": now.isoformat(),
        "completed_at": None,
    }
    if redis is not None:
        await redis.setex(f"import:{import_id}", _IMPORT_KEY_TTL, json.dumps(initial))

    background_tasks.add_task(
        _process_import, import_id, rows, tenant, supplier_repo, redis, now
    )
    return DataResponse(
        data=ImportJobResponse(
            import_id=import_id,
            status="processing",
            total_rows=len(rows),
            poll_url=f"/api/v1/portfolio/imports/{import_id}",
            submitted_at=now,
        )
    )


@router.patch(
    "/suppliers/{portfolio_supplier_id}",
    response_model=DataResponse[PatchPortfolioSupplierResponse],
)
async def patch_supplier(
    portfolio_supplier_id: str,
    body: PatchPortfolioSupplierRequest,
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
) -> DataResponse[PatchPortfolioSupplierResponse]:
    """Update custom_name, internal_id, or tags for a portfolio entry."""
    record = await supplier_repo.patch_portfolio_supplier(
        tenant.tenant_id, portfolio_supplier_id, body
    )
    return DataResponse(
        data=PatchPortfolioSupplierResponse(
            portfolio_supplier_id=record.portfolio_supplier_id,
            custom_name=record.custom_name,
            internal_id=record.internal_id,
            tags=record.tags,
            updated_at=record.updated_at,
        )
    )


@router.delete("/suppliers/{portfolio_supplier_id}", status_code=204)
async def remove_supplier(
    portfolio_supplier_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
) -> Response:
    """Remove a supplier from the portfolio. Returns 204 No Content."""
    await supplier_repo.remove_from_portfolio(tenant.tenant_id, portfolio_supplier_id)
    return Response(status_code=204)


@router.get("/imports/{import_id}", response_model=DataResponse[ImportStatusResponse])
async def get_import_status(
    import_id: str,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
) -> DataResponse[ImportStatusResponse]:
    """Poll the status of a bulk CSV import job."""
    from backend.app.models.errors import ImportNotFoundError

    redis = _get_redis(request)
    if redis is None:
        raise ImportNotFoundError(import_id)

    raw = await redis.get(f"import:{import_id}")
    if raw is None:
        raise ImportNotFoundError(import_id)

    state: dict[str, Any] = json.loads(raw)
    return DataResponse(
        data=ImportStatusResponse(
            import_id=state["import_id"],
            status=state["status"],
            total_rows=state["total_rows"],
            resolved_count=state["resolved_count"],
            added_count=state["added_count"],
            duplicate_count=state["duplicate_count"],
            unresolved_count=state["unresolved_count"],
            error_count=state["error_count"],
            plan_limit_skipped_count=state["plan_limit_skipped_count"],
            unresolved_items=state.get("unresolved_items", []),
            started_at=datetime.fromisoformat(state["started_at"]),
            completed_at=(
                datetime.fromisoformat(state["completed_at"])
                if state.get("completed_at")
                else None
            ),
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_redis(request: Request) -> Any:
    """Return Redis client from app.state, or None if unavailable."""
    state = getattr(getattr(request, "app", None), "state", None)
    return getattr(state, "redis", None) if state else None


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    """Decode raw CSV bytes into a list of row dicts."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


async def _process_import(
    import_id: str,
    rows: list[dict[str, str]],
    tenant: TenantContext,
    supplier_repo: SupplierRepository,
    redis: Any,
    started_at: datetime,
) -> None:
    """Background task: resolve each CSV row and add it to the portfolio."""
    from backend.app.models.errors import (
        PlanLimitExceededError,
        SupplierAlreadyInPortfolioError,
        SupplierNotFoundError,
    )

    resolved_count = 0
    added_count = 0
    duplicate_count = 0
    unresolved_count = 0
    error_count = 0
    plan_limit_skipped_count = 0
    unresolved_items: list[dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        raw_name = (row.get("name") or "").strip()
        if not raw_name:
            error_count += 1
            continue

        country = (row.get("country") or "").strip().upper() or None
        internal_id = (row.get("internal_id") or "").strip() or None
        tags_raw = (row.get("tags") or "").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()][:10]

        try:
            await check_supplier_limit(tenant, supplier_repo)
        except PlanLimitExceededError:
            plan_limit_skipped_count += 1
            continue

        try:
            req = AddSupplierRequest(
                raw_name=raw_name,
                country_hint=country,
                internal_id=internal_id,
                tags=tags,
            )
            await supplier_repo.add_to_portfolio(tenant.tenant_id, req)
            resolved_count += 1
            added_count += 1
        except SupplierNotFoundError:
            unresolved_count += 1
            unresolved_items.append(
                {
                    "row": idx,
                    "raw_name": raw_name,
                    "country": country,
                    "reason": "no_match",
                    "best_candidate": None,
                    "best_confidence": None,
                }
            )
        except SupplierAlreadyInPortfolioError:
            resolved_count += 1
            duplicate_count += 1
        except Exception as exc:
            log.warning("import.row_error", row=idx, error=str(exc))
            error_count += 1

    completed_at = datetime.now(tz=timezone.utc)
    final: dict[str, Any] = {
        "import_id": import_id,
        "status": "completed",
        "total_rows": len(rows),
        "resolved_count": resolved_count,
        "added_count": added_count,
        "duplicate_count": duplicate_count,
        "unresolved_count": unresolved_count,
        "error_count": error_count,
        "plan_limit_skipped_count": plan_limit_skipped_count,
        "unresolved_items": unresolved_items,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }
    if redis is not None:
        await redis.setex(f"import:{import_id}", _IMPORT_KEY_TTL, json.dumps(final))
