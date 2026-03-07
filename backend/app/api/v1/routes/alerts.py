"""Alert routes — list and patch alert status.

GET   /api/v1/alerts
PATCH /api/v1/alerts/{alert_id}

State machine transitions are validated inside the repository (not here).
Viewers can read alerts but cannot change status — enforced here.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends

from backend.app.dependencies import (
    TenantContext,
    get_alert_repository,
    get_current_tenant,
)
from backend.app.models.errors import ForbiddenError
from backend.app.models.requests import AlertsListParams, PatchAlertRequest
from backend.app.models.responses import (
    AlertResponse,
    DataResponse,
    ListResponse,
    Meta,
    PatchAlertResponse,
)
from backend.app.repositories.alert_repository import AlertRepository

log = structlog.get_logger()
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=ListResponse[AlertResponse])
async def list_alerts(
    params: AlertsListParams = Depends(),
    tenant: TenantContext = Depends(get_current_tenant),
    repo: AlertRepository = Depends(get_alert_repository),
) -> ListResponse[AlertResponse]:
    """Paginated list of alerts for the tenant with optional filters."""
    status_filter = None if params.status == "all" else params.status
    items, total = await repo.list_alerts(
        tenant_id=tenant.tenant_id,
        status=status_filter,
        severity=params.severity,
        supplier_id=params.supplier_id,
        alert_type=params.alert_type,
        page=params.page,
        per_page=params.per_page,
    )
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


@router.patch("/{alert_id}", response_model=DataResponse[PatchAlertResponse])
async def patch_alert(
    alert_id: str,
    body: PatchAlertRequest,
    tenant: TenantContext = Depends(get_current_tenant),
    repo: AlertRepository = Depends(get_alert_repository),
) -> DataResponse[PatchAlertResponse]:
    """Update alert status or note. Invalid state transitions return 422."""
    if tenant.role != "admin" and body.status is not None:
        raise ForbiddenError("Viewers cannot change alert status.")
    result = await repo.patch_alert(
        tenant_id=tenant.tenant_id,
        alert_id=alert_id,
        request=body,
    )
    return DataResponse(data=result)
