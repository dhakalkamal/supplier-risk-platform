"""Tests for X-Request-ID middleware and rate limiting."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from backend.app.dependencies import (
    TenantContext,
    get_alert_repository,
    get_current_tenant,
    get_news_repository,
    get_score_repository,
    get_settings_repository,
    get_supplier_repository,
)
from backend.app.main import create_app
from backend.app.repositories.alert_repository import InMemoryAlertRepository
from backend.app.repositories.news_repository import InMemoryNewsRepository
from backend.app.repositories.score_repository import InMemoryScoreRepository
from backend.app.repositories.settings_repository import InMemorySettingsRepository
from backend.app.repositories.supplier_repository import InMemorySupplierRepository


def _make_fake_jwt(tenant_id: str = "10000000-0000-0000-0000-000000000001") -> str:
    """Build a structurally valid JWT with no real signature (for rate-limit extraction)."""
    hdr = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    pay = base64.urlsafe_b64encode(
        json.dumps({"tenant_id": tenant_id, "sub": "usr_test"}).encode()
    ).decode().rstrip("=")
    return f"{hdr}.{pay}.fakesig"


_TENANT = TenantContext(
    tenant_id="10000000-0000-0000-0000-000000000001",
    user_id="usr_adminuser001",
    role="admin",
    plan="enterprise",
    email="admin@test.example",
)


def _make_test_client(redis: object = None) -> TestClient:
    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    app.state.redis = redis
    app.dependency_overrides[get_current_tenant] = lambda: _TENANT
    app.dependency_overrides[get_supplier_repository] = lambda: InMemorySupplierRepository()
    app.dependency_overrides[get_score_repository] = lambda: InMemoryScoreRepository()
    app.dependency_overrides[get_alert_repository] = lambda: InMemoryAlertRepository()
    app.dependency_overrides[get_news_repository] = lambda: InMemoryNewsRepository()
    app.dependency_overrides[get_settings_repository] = lambda: InMemorySettingsRepository()
    with TestClient(app, raise_server_exceptions=True) as c:
        return c


class TestRequestIDMiddleware:
    def test_request_id_header_present_on_every_response(self) -> None:
        client = _make_test_client()
        resp = client.get("/health")
        assert "x-request-id" in resp.headers

    def test_echoes_client_provided_request_id(self) -> None:
        client = _make_test_client()
        resp = client.get("/health", headers={"X-Request-ID": "my-custom-id-123"})
        assert resp.headers["x-request-id"] == "my-custom-id-123"

    def test_generates_request_id_when_not_provided(self) -> None:
        client = _make_test_client()
        resp = client.get("/health")
        req_id = resp.headers["x-request-id"]
        assert req_id.startswith("req_")
        assert len(req_id) > 4


class TestRateLimitMiddleware:
    def test_rate_limit_headers_on_response(self) -> None:
        """Rate limit headers should appear when a valid JWT tenant_id is present."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[0, 1, 1, True])
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        client = _make_test_client(redis=mock_redis)
        # Pass a fake JWT so the middleware can extract tenant_id for rate limiting
        resp = client.get(
            "/api/v1/portfolio/suppliers",
            headers={"Authorization": f"Bearer {_make_fake_jwt()}"},
        )
        assert "x-ratelimit-limit" in resp.headers
        assert "x-ratelimit-remaining" in resp.headers
        assert "x-ratelimit-reset" in resp.headers

    def test_rate_limit_429_when_limit_exceeded(self) -> None:
        """When ZCARD returns >1000, the middleware returns 429 RATE_LIMITED."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        # ZCARD returns 1001 — one over the 1000 req/min limit
        mock_pipeline.execute = AsyncMock(return_value=[0, 1, 1001, True])
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        client = _make_test_client(redis=mock_redis)
        resp = client.get(
            "/api/v1/portfolio/suppliers",
            headers={"Authorization": f"Bearer {_make_fake_jwt()}"},
        )
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "RATE_LIMITED"

    def test_health_exempt_from_rate_limit(self) -> None:
        """/health is exempt — no rate limit headers, always 200."""
        mock_redis = MagicMock()
        mock_pipeline = AsyncMock()
        mock_pipeline.execute = AsyncMock(return_value=[0, 1, 9999, True])
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        client = _make_test_client(redis=mock_redis)
        resp = client.get("/health")
        assert resp.status_code == 200


class TestErrorHandler:
    def test_unhandled_exception_returns_500_not_stack_trace(self) -> None:
        """Unhandled exceptions must return INTERNAL_ERROR, not a raw traceback."""

        app = create_app()
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()
        app.state.redis = None
        app.dependency_overrides[get_current_tenant] = lambda: _TENANT

        # Inject a broken supplier repository that raises on every call
        broken_repo = InMemorySupplierRepository()
        broken_repo.get_portfolio_suppliers = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("Unexpected database failure")
        )
        app.dependency_overrides[get_supplier_repository] = lambda: broken_repo
        app.dependency_overrides[get_score_repository] = lambda: InMemoryScoreRepository()
        app.dependency_overrides[get_alert_repository] = lambda: InMemoryAlertRepository()
        app.dependency_overrides[get_news_repository] = lambda: InMemoryNewsRepository()
        app.dependency_overrides[get_settings_repository] = lambda: InMemorySettingsRepository()

        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/v1/portfolio/suppliers")

        assert resp.status_code == 500
        error = resp.json()["error"]
        assert error["code"] == "INTERNAL_ERROR"
        # Must NOT expose stack trace or raw exception message to client
        assert "Unexpected database failure" not in resp.text
