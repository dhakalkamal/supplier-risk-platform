"""Plan limit enforcement.

Call check_supplier_limit() before every add-to-portfolio operation.
Call check_user_limit() before every invite operation.
Both raise PlanLimitExceededError on breach.
"""

from __future__ import annotations

from backend.app.dependencies import TenantContext
from backend.app.models.errors import PlanLimitExceededError
from backend.app.repositories.supplier_repository import SupplierRepository

#: Max suppliers and users per plan. None = unlimited.
PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "starter":    {"suppliers": 25,  "users": 3},
    "growth":     {"suppliers": 100, "users": 10},
    "pro":        {"suppliers": 500, "users": None},
    "enterprise": {"suppliers": None, "users": None},
}


async def check_supplier_limit(
    tenant: TenantContext,
    repo: SupplierRepository,
) -> None:
    """Raise PlanLimitExceededError if the tenant is at the supplier limit.

    Must be called before every add-to-portfolio operation.
    """
    limit = PLAN_LIMITS[tenant.plan]["suppliers"]
    if limit is None:
        return  # unlimited

    current = await repo.count_portfolio(tenant.tenant_id)
    if current >= limit:
        raise PlanLimitExceededError(
            resource="suppliers",
            current_count=current,
            plan_limit=limit,
            current_plan=tenant.plan,
        )


async def check_user_limit(
    tenant: TenantContext,
    current_user_count: int,
) -> None:
    """Raise PlanLimitExceededError if the tenant is at the user limit.

    current_user_count must be fetched by the caller before invoking this.
    """
    limit = PLAN_LIMITS[tenant.plan]["users"]
    if limit is None:
        return  # unlimited

    if current_user_count >= limit:
        raise PlanLimitExceededError(
            resource="users",
            current_count=current_user_count,
            plan_limit=limit,
            current_plan=tenant.plan,
        )
