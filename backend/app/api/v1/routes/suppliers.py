"""Supplier routes — profile, score history, news, and entity resolution.

GET  /api/v1/suppliers/{supplier_id}
GET  /api/v1/suppliers/{supplier_id}/score-history
GET  /api/v1/suppliers/{supplier_id}/news
POST /api/v1/suppliers/resolve

Note: /resolve is declared before /{supplier_id} to avoid path conflicts.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request

from backend.app.dependencies import (
    TenantContext,
    get_current_tenant,
    get_db_pool,
    get_news_repository,
    get_score_repository,
    get_supplier_repository,
)
from backend.app.models.errors import SupplierNotFoundError
from backend.app.models.requests import (
    ResolveSupplierRequest,
    ScoreHistoryParams,
    SupplierNewsParams,
)
from backend.app.models.responses import (
    CurrentScore,
    DataResponse,
    ListResponse,
    Meta,
    NewsArticleResponse,
    ResolveSupplierResponse,
    ScoreHistoryItem,
    ScoreHistoryResponse,
    SignalCategoryBreakdown,
    SupplierProfile,
)
from backend.app.repositories.news_repository import NewsRepository
from backend.app.repositories.score_repository import ScoreRepository, SupplierScore
from backend.app.repositories.supplier_repository import SupplierRepository
from backend.app.services.resolution_service import resolve_supplier

log = structlog.get_logger()
router = APIRouter(prefix="/suppliers", tags=["suppliers"])

_PROFILE_CACHE_TTL = 3600  # 1 hour


@router.post("/resolve", response_model=DataResponse[ResolveSupplierResponse])
async def resolve(
    body: ResolveSupplierRequest,
    tenant: TenantContext = Depends(get_current_tenant),
    pool: Any = Depends(get_db_pool),
) -> DataResponse[ResolveSupplierResponse]:
    """Resolve a raw company name to a canonical supplier record."""
    result = await resolve_supplier(body, pool)
    return DataResponse(data=result)


@router.get("/{supplier_id}", response_model=DataResponse[SupplierProfile])
async def get_supplier(
    supplier_id: str,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
    score_repo: ScoreRepository = Depends(get_score_repository),
) -> DataResponse[SupplierProfile]:
    """Full supplier profile with current risk score. Cached in Redis for 1 hour."""
    redis = _get_redis(request)
    cache_key = f"supplier_profile:{supplier_id}"

    if redis is not None:
        cached = await redis.get(cache_key)
        if cached:
            return DataResponse(data=SupplierProfile.model_validate_json(cached))

    fetched = await supplier_repo.get_by_id(supplier_id, tenant_id=tenant.tenant_id)
    if fetched is None:
        raise SupplierNotFoundError(supplier_id)

    latest = await score_repo.get_latest_score(supplier_id)
    if latest is not None:
        fetched = fetched.model_copy(
            update={"current_score": _build_current_score(latest)}
        )
    profile: SupplierProfile = fetched

    if redis is not None:
        await redis.setex(cache_key, _PROFILE_CACHE_TTL, profile.model_dump_json())

    return DataResponse(data=profile)


@router.get(
    "/{supplier_id}/score-history",
    response_model=DataResponse[ScoreHistoryResponse],
)
async def get_score_history(
    supplier_id: str,
    params: ScoreHistoryParams = Depends(),
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
    score_repo: ScoreRepository = Depends(get_score_repository),
) -> DataResponse[ScoreHistoryResponse]:
    """Score history for the past N days (default 90, max 365)."""
    exists = await supplier_repo.get_by_id(supplier_id)
    if exists is None:
        raise SupplierNotFoundError(supplier_id)

    scores = await score_repo.get_score_history(supplier_id, params.days)
    items = [
        ScoreHistoryItem(
            date=s.score_date,
            score=s.score,
            risk_level=s.risk_level,  # type: ignore[arg-type]
            model_version=s.model_version,
        )
        for s in scores
    ]
    return DataResponse(
        data=ScoreHistoryResponse(
            supplier_id=supplier_id,
            days_requested=params.days,
            days_available=len(items),
            scores=items,
        )
    )


@router.get("/{supplier_id}/news", response_model=ListResponse[NewsArticleResponse])
async def get_supplier_news(
    supplier_id: str,
    params: SupplierNewsParams = Depends(),
    tenant: TenantContext = Depends(get_current_tenant),
    supplier_repo: SupplierRepository = Depends(get_supplier_repository),
    news_repo: NewsRepository = Depends(get_news_repository),
) -> ListResponse[NewsArticleResponse]:
    """Paginated news articles for a supplier, newest first."""
    exists = await supplier_repo.get_by_id(supplier_id)
    if exists is None:
        raise SupplierNotFoundError(supplier_id)

    articles, total = await news_repo.get_supplier_news(supplier_id, params)
    total_pages = max(1, (total + params.per_page - 1) // params.per_page)
    return ListResponse(
        data=articles,
        meta=Meta(
            total=total,
            page=params.page,
            per_page=params.per_page,
            total_pages=total_pages,
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_redis(request: Request) -> Any:
    """Extract Redis client from app.state — returns None if unavailable."""
    state = getattr(getattr(request, "app", None), "state", None)
    return getattr(state, "redis", None) if state else None


def _build_current_score(s: SupplierScore) -> CurrentScore:
    """Convert a SupplierScore domain model into a CurrentScore API model."""
    breakdown: dict[str, SignalCategoryBreakdown] = {}
    for category, cat_data in (s.signal_breakdown or {}).items():
        if isinstance(cat_data, dict):
            breakdown[category] = SignalCategoryBreakdown(
                score=cat_data.get("score", 0),
                weight=cat_data.get("weight", 0.0),
                data_available=cat_data.get("data_available", False),
            )
    return CurrentScore(
        score=s.score,
        risk_level=s.risk_level,  # type: ignore[arg-type]
        model_version=s.model_version,
        scored_at=s.scored_at,
        data_completeness=s.data_completeness,
        signal_breakdown=breakdown,
        top_drivers=[],
    )
