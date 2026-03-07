"""Exception handlers — map domain exceptions to standard API error envelopes.

All error responses follow the format defined in API_SPEC.md Section 4:
  {"error": {"code": "...", "message": "...", "request_id": "...", "details": {...}}}

Register by calling register_exception_handlers(app) in main.py.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.app.models.errors import (
    AlertNotFoundError,
    ForbiddenError,
    ImportInvalidFormatError,
    ImportNotFoundError,
    InvalidStateTransitionError,
    PlanLimitExceededError,
    PortfolioSupplierNotFoundError,
    ResolutionFailedError,
    SupplierAlreadyInPortfolioError,
    SupplierNotFoundError,
    UserAlreadyExistsError,
)

log = structlog.get_logger()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _error_json(
    code: str,
    message: str,
    request_id: str,
    details: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Register all domain exception → HTTP response mappings on the app."""

    @app.exception_handler(SupplierNotFoundError)
    async def supplier_not_found(request: Request, exc: SupplierNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_json("SUPPLIER_NOT_FOUND", str(exc), _request_id(request)),
        )

    @app.exception_handler(AlertNotFoundError)
    async def alert_not_found(request: Request, exc: AlertNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_json("ALERT_NOT_FOUND", str(exc), _request_id(request)),
        )

    @app.exception_handler(PortfolioSupplierNotFoundError)
    async def portfolio_not_found(
        request: Request, exc: PortfolioSupplierNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_json("NOT_FOUND", str(exc), _request_id(request)),
        )

    @app.exception_handler(ImportNotFoundError)
    async def import_not_found(request: Request, exc: ImportNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_json("IMPORT_NOT_FOUND", str(exc), _request_id(request)),
        )

    @app.exception_handler(SupplierAlreadyInPortfolioError)
    async def already_in_portfolio(
        request: Request, exc: SupplierAlreadyInPortfolioError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error_json(
                "SUPPLIER_ALREADY_IN_PORTFOLIO", str(exc), _request_id(request)
            ),
        )

    @app.exception_handler(UserAlreadyExistsError)
    async def user_already_exists(
        request: Request, exc: UserAlreadyExistsError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error_json("USER_ALREADY_EXISTS", str(exc), _request_id(request)),
        )

    @app.exception_handler(ResolutionFailedError)
    async def resolution_failed(
        request: Request, exc: ResolutionFailedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_json("RESOLUTION_FAILED", str(exc), _request_id(request)),
        )

    @app.exception_handler(ImportInvalidFormatError)
    async def import_invalid_format(
        request: Request, exc: ImportInvalidFormatError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_json(
                "IMPORT_INVALID_FORMAT", str(exc), _request_id(request)
            ),
        )

    @app.exception_handler(InvalidStateTransitionError)
    async def invalid_transition(
        request: Request, exc: InvalidStateTransitionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_json(
                "INVALID_STATE_TRANSITION",
                str(exc),
                _request_id(request),
                details={
                    "current_status": exc.current_status,
                    "requested_status": exc.requested_status,
                    "allowed_transitions": ", ".join(exc.allowed_transitions),
                },
            ),
        )

    @app.exception_handler(PlanLimitExceededError)
    async def plan_limit_exceeded(
        request: Request, exc: PlanLimitExceededError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=_error_json(
                "PLAN_LIMIT_EXCEEDED",
                str(exc),
                _request_id(request),
                details={
                    "current_count": str(exc.current_count),
                    "plan_limit": str(exc.plan_limit),
                    "current_plan": exc.current_plan,
                    "upgrade_url": "https://app.yourdomain.com/settings/billing",
                },
            ),
        )

    @app.exception_handler(ForbiddenError)
    async def forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=_error_json("FORBIDDEN", str(exc), _request_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {
            str(e["loc"][-1] if e["loc"] else "body"): e["msg"] for e in exc.errors()
        }
        return JSONResponse(
            status_code=422,
            content=_error_json(
                "VALIDATION_ERROR",
                "Request validation failed.",
                _request_id(request),
                details=details,
            ),
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_error(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        details = {
            str(e["loc"][-1] if e["loc"] else "body"): e["msg"] for e in exc.errors()
        }
        return JSONResponse(
            status_code=422,
            content=_error_json(
                "VALIDATION_ERROR",
                "Request validation failed.",
                _request_id(request),
                details=details,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled_exception",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
            path=request.url.path,
            request_id=_request_id(request),
        )
        return JSONResponse(
            status_code=500,
            content=_error_json(
                "INTERNAL_ERROR",
                "An unexpected error occurred. Our team has been notified.",
                _request_id(request),
            ),
        )
