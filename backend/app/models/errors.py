"""Error response models and domain exception classes.

API error responses always use the ErrorResponse envelope (API_SPEC.md Section 4).
Domain exceptions are raised by services/repositories and converted to HTTP responses
by the exception handlers in backend/app/middleware/error_handler.py.
"""

from __future__ import annotations

from pydantic import BaseModel

# =============================================================================
# API Response Models
# =============================================================================


class ErrorDetail(BaseModel):
    """Structured error payload embedded in every error response envelope."""

    code: str
    message: str
    request_id: str
    details: dict[str, str] = {}


class ErrorResponse(BaseModel):
    """Top-level error envelope — all 4xx/5xx responses use this shape."""

    error: ErrorDetail


# =============================================================================
# Domain Exceptions
# Raised by repositories and services; converted to HTTP responses by the
# exception handlers registered in error_handler.py.
# =============================================================================


class SupplierNotFoundError(Exception):
    """Supplier ID does not exist in the registry or the tenant's portfolio."""

    def __init__(self, supplier_id: str) -> None:
        self.supplier_id = supplier_id
        super().__init__(f"Supplier {supplier_id!r} not found.")


class AlertNotFoundError(Exception):
    """Alert ID does not exist or does not belong to the requesting tenant."""

    def __init__(self, alert_id: str) -> None:
        self.alert_id = alert_id
        super().__init__(f"Alert {alert_id!r} not found.")


class PortfolioSupplierNotFoundError(Exception):
    """portfolio_supplier_id does not exist or does not belong to the tenant."""

    def __init__(self, portfolio_supplier_id: str) -> None:
        self.portfolio_supplier_id = portfolio_supplier_id
        super().__init__(f"Portfolio supplier {portfolio_supplier_id!r} not found.")


class SupplierAlreadyInPortfolioError(Exception):
    """Supplier is already tracked in the tenant's portfolio."""

    def __init__(self, supplier_id: str) -> None:
        self.supplier_id = supplier_id
        super().__init__(f"Supplier {supplier_id!r} is already in your portfolio.")


class ResolutionFailedError(Exception):
    """Entity resolution could not match the raw name to a canonical supplier."""

    def __init__(self, raw_name: str) -> None:
        self.raw_name = raw_name
        super().__init__(f"Could not resolve supplier name {raw_name!r}.")


class InvalidStateTransitionError(Exception):
    """Alert status transition is not permitted by the state machine."""

    def __init__(
        self,
        current_status: str,
        requested_status: str,
        allowed_transitions: list[str],
    ) -> None:
        self.current_status = current_status
        self.requested_status = requested_status
        self.allowed_transitions = allowed_transitions
        super().__init__(
            f"Cannot transition alert from {current_status!r} to {requested_status!r}. "
            f"Allowed: {allowed_transitions}"
        )


class PlanLimitExceededError(Exception):
    """Operation would exceed the tenant's plan limit (suppliers or users)."""

    def __init__(
        self,
        resource: str,
        current_count: int,
        plan_limit: int,
        current_plan: str,
    ) -> None:
        self.resource = resource
        self.current_count = current_count
        self.plan_limit = plan_limit
        self.current_plan = current_plan
        super().__init__(
            f"Plan limit exceeded for {resource}: {current_count}/{plan_limit} "
            f"on {current_plan!r} plan."
        )


class ForbiddenError(Exception):
    """Authenticated user does not have sufficient role for this action."""

    def __init__(self, action: str = "perform this action") -> None:
        self.action = action
        super().__init__(f"Forbidden: insufficient permissions to {action}.")


class UserAlreadyExistsError(Exception):
    """Email is already associated with a user in this tenant."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"User {email!r} already exists in this tenant.")


class ImportNotFoundError(Exception):
    """Import job ID does not exist or does not belong to the tenant."""

    def __init__(self, import_id: str) -> None:
        self.import_id = import_id
        super().__init__(f"Import job {import_id!r} not found.")


class ImportInvalidFormatError(Exception):
    """CSV file failed format validation (missing header, too large, etc.)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid import file: {reason}")
