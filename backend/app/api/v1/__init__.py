"""API v1 package — aggregates all v1 route modules into a single router."""

from fastapi import APIRouter

from backend.app.api.v1.routes.alerts import router as alerts_router
from backend.app.api.v1.routes.portfolio import router as portfolio_router
from backend.app.api.v1.routes.settings import router as settings_router
from backend.app.api.v1.routes.suppliers import router as suppliers_router

api_v1_router = APIRouter()
api_v1_router.include_router(portfolio_router)
api_v1_router.include_router(suppliers_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(settings_router)
