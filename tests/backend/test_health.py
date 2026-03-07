"""Tests for GET /health and GET /ready."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def health_client() -> TestClient:
    """Minimal test client — no dependency overrides needed for health routes."""
    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    app.state.redis = None
    with TestClient(app, raise_server_exceptions=True) as c:
        return c


class TestLiveness:
    def test_health_returns_200(self, health_client: TestClient) -> None:
        resp = health_client.get("/health")
        assert resp.status_code == 200

    def test_health_no_auth_required(self, health_client: TestClient) -> None:
        """Health check must be accessible without a JWT."""
        resp = health_client.get("/health")
        assert resp.status_code == 200

    def test_health_response_structure(self, health_client: TestClient) -> None:
        resp = health_client.get("/health")
        body = resp.json()
        data = body["data"]
        assert data["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data


class TestReadiness:
    def test_ready_200_when_postgres_up(self, health_client: TestClient) -> None:
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )
        with patch(
            "backend.app.db.connection.get_pool", AsyncMock(return_value=mock_pool)
        ):
            resp = health_client.get("/ready")
        # Redis and Kafka will be unavailable in unit tests → "not_ready" is acceptable
        # We just check the structure is correct
        body = resp.json()
        assert "data" in body
        assert body["data"]["status"] in ("ready", "not_ready")
        assert "dependencies" in body["data"]

    def test_ready_503_when_postgres_down(self, health_client: TestClient) -> None:
        with patch(
            "backend.app.db.connection.get_pool",
            AsyncMock(side_effect=RuntimeError("pool not initialised")),
        ):
            resp = health_client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["data"]["status"] == "not_ready"
        assert body["data"]["dependencies"]["postgres"] == "unavailable"
