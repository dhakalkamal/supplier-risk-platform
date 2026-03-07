"""Settings routes — alert rules and user management.

GET    /api/v1/settings/alert-rules
PUT    /api/v1/settings/alert-rules          (admin only)
GET    /api/v1/settings/users                (admin only)
POST   /api/v1/settings/users/invite         (admin only)
DELETE /api/v1/settings/users/{user_id}      (admin only, cannot delete self)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, Query

from backend.app.dependencies import (
    TenantContext,
    get_current_tenant,
    get_settings_repository,
    require_admin,
)
from backend.app.models.errors import ForbiddenError, UserAlreadyExistsError
from backend.app.models.requests import AlertRulesRequest, InviteUserRequest
from backend.app.models.responses import (
    AlertRulesResponse,
    DataResponse,
    InviteResponse,
    ListResponse,
    Meta,
    UserResponse,
)
from backend.app.repositories.settings_repository import SettingsRepository
from backend.app.services.plan_limits import check_user_limit

log = structlog.get_logger()
router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/alert-rules", response_model=DataResponse[AlertRulesResponse])
async def get_alert_rules(
    tenant: TenantContext = Depends(get_current_tenant),
    repo: SettingsRepository = Depends(get_settings_repository),
) -> DataResponse[AlertRulesResponse]:
    """Return the tenant's alert rule configuration (defaults if never set)."""
    rules = await repo.get_alert_rules(tenant.tenant_id)
    return DataResponse(data=rules)


@router.put("/alert-rules", response_model=DataResponse[AlertRulesResponse])
async def update_alert_rules(
    body: AlertRulesRequest,
    tenant: TenantContext = Depends(require_admin),
    repo: SettingsRepository = Depends(get_settings_repository),
) -> DataResponse[AlertRulesResponse]:
    """Create or replace alert rules for the tenant. Admin only."""
    rules = await repo.upsert_alert_rules(tenant.tenant_id, body)
    return DataResponse(data=rules)


@router.get("/users", response_model=ListResponse[UserResponse])
async def list_users(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    tenant: TenantContext = Depends(require_admin),
    repo: SettingsRepository = Depends(get_settings_repository),
) -> ListResponse[UserResponse]:
    """List all users in the tenant. Admin only."""
    users, total = await repo.list_users(
        tenant_id=tenant.tenant_id,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    return ListResponse(
        data=users,
        meta=Meta(total=total, page=page, per_page=per_page, total_pages=total_pages),
    )


@router.post("/users/invite", response_model=DataResponse[InviteResponse], status_code=201)
async def invite_user(
    body: InviteUserRequest,
    tenant: TenantContext = Depends(require_admin),
    repo: SettingsRepository = Depends(get_settings_repository),
) -> DataResponse[InviteResponse]:
    """Invite a new user to the tenant. Admin only. Enforces plan user limit."""
    current_count = await repo.count_users(tenant.tenant_id)
    await check_user_limit(tenant, current_count)

    email_str = str(body.email)
    if await repo.user_exists_by_email(tenant.tenant_id, email_str):
        raise UserAlreadyExistsError(email_str)

    invite_id = "inv_" + uuid.uuid4().hex
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)
    return DataResponse(
        data=InviteResponse(
            invite_id=invite_id,
            email=email_str,
            role=body.role,
            expires_at=expires_at,
        )
    )


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    tenant: TenantContext = Depends(require_admin),
    repo: SettingsRepository = Depends(get_settings_repository),
) -> None:
    """Remove a user from the tenant. Admin only. Cannot delete own account."""
    if user_id == tenant.user_id:
        raise ForbiddenError("You cannot delete your own account.")
    await repo.delete_user(user_id=user_id, tenant_id=tenant.tenant_id)
