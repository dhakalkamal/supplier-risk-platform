"""Tests for WebSocket endpoint and WebSocketManager.

Auth strategy: auth0_domain is empty in tests → dev bypass accepts any non-empty token.
For auth-failure tests, _authenticate_ws is patched to return None.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
from backend.app.services.websocket_manager import WebSocketManager

_TENANT_ID = "10000000-0000-0000-0000-000000000001"

_TENANT = TenantContext(
    tenant_id=_TENANT_ID,
    user_id="usr_adminuser001",
    role="admin",
    plan="enterprise",
    email="admin@test.example",
)


def _make_ws_client() -> TestClient:
    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    app.state.redis = None
    app.dependency_overrides[get_current_tenant] = lambda: _TENANT
    app.dependency_overrides[get_supplier_repository] = lambda: InMemorySupplierRepository()
    app.dependency_overrides[get_score_repository] = lambda: InMemoryScoreRepository()
    app.dependency_overrides[get_alert_repository] = lambda: InMemoryAlertRepository()
    app.dependency_overrides[get_news_repository] = lambda: InMemoryNewsRepository()
    app.dependency_overrides[get_settings_repository] = lambda: InMemorySettingsRepository()
    with TestClient(app, raise_server_exceptions=False) as c:
        return c


class TestWebSocketAuth:
    def test_connection_rejected_when_no_token(self) -> None:
        """Missing token → server closes with 1008 before accepting."""
        client = _make_ws_client()
        with pytest.raises(Exception):
            with client.websocket_connect("/api/v1/ws/alerts"):
                pass

    def test_connection_rejected_when_auth_fails(self) -> None:
        """_authenticate_ws returning None → close with 1008."""
        client = _make_ws_client()
        with patch(
            "backend.app.api.v1.routes.websocket._authenticate_ws",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(Exception):
                with client.websocket_connect("/api/v1/ws/alerts?token=badtoken"):
                    pass

    def test_connection_accepted_with_valid_token(self) -> None:
        """Dev bypass accepts any non-empty token. listen_for_events patched to exit."""
        client = _make_ws_client()
        with patch(
            "backend.app.services.websocket_manager.WebSocketManager.listen_for_events",
            new=AsyncMock(return_value=None),
        ):
            with client.websocket_connect("/api/v1/ws/alerts?token=any-token") as ws:
                # Connection was accepted — we can close cleanly from client side
                ws.close()


class TestWebSocketConnectionLimit:
    def test_sixth_connection_rejected(self) -> None:
        """WebSocketManager enforces max 5 connections per tenant."""
        manager = WebSocketManager()
        # Dev bypass tenant ID (returned by _authenticate_ws when auth0_domain is empty)
        dev_tenant = "dev-tenant-00000000-0000-0000-0000-000000000000"
        manager._connections[dev_tenant] = {MagicMock() for _ in range(5)}

        client = _make_ws_client()
        with patch(
            "backend.app.api.v1.routes.websocket.websocket_manager",
            manager,
        ):
            with pytest.raises(Exception):
                with client.websocket_connect("/api/v1/ws/alerts?token=any"):
                    pass


class TestWebSocketMessages:
    def test_alert_fired_message_received_from_pubsub(self) -> None:
        """listen_for_events sends an alert.fired message — client receives it."""
        alert_msg = json.dumps({"type": "alert.fired", "payload": {"alert_id": "alr_001"}})

        async def _fake_listen(
            self_mgr: WebSocketManager, websocket: object, tenant_id: str
        ) -> None:
            ws = websocket  # type: ignore[assignment]
            await ws.send_text(alert_msg)  # type: ignore[attr-defined]

        client = _make_ws_client()
        with patch.object(WebSocketManager, "listen_for_events", _fake_listen):
            with client.websocket_connect("/api/v1/ws/alerts?token=any") as ws:
                data = json.loads(ws.receive_text())
                assert data["type"] == "alert.fired"
                assert data["payload"]["alert_id"] == "alr_001"

    def test_score_updated_message_received_from_pubsub(self) -> None:
        """listen_for_events sends a score.updated message — client receives it."""
        score_msg = json.dumps({"type": "score.updated", "payload": {"score": 75}})

        async def _fake_listen(
            self_mgr: WebSocketManager, websocket: object, tenant_id: str
        ) -> None:
            await websocket.send_text(score_msg)  # type: ignore[attr-defined]

        client = _make_ws_client()
        with patch.object(WebSocketManager, "listen_for_events", _fake_listen):
            with client.websocket_connect("/api/v1/ws/alerts?token=any") as ws:
                data = json.loads(ws.receive_text())
                assert data["type"] == "score.updated"
                assert data["payload"]["score"] == 75


class TestWebSocketManager:
    async def test_connect_returns_false_at_limit(self) -> None:
        """connect() returns False without accepting when limit exceeded."""
        manager = WebSocketManager()
        manager._connections[_TENANT_ID] = {MagicMock() for _ in range(5)}

        mock_ws = AsyncMock()
        result = await manager.connect(mock_ws, _TENANT_ID)
        assert result is False
        mock_ws.accept.assert_not_called()

    async def test_broadcast_sends_to_all_connections(self) -> None:
        """broadcast_to_tenant sends JSON to every connected WebSocket."""
        manager = WebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        manager._connections[_TENANT_ID] = {ws1, ws2}

        await manager.broadcast_to_tenant(_TENANT_ID, {"type": "ping"})

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        sent = json.loads(ws1.send_text.call_args[0][0])
        assert sent["type"] == "ping"

    async def test_disconnect_removes_connection(self) -> None:
        manager = WebSocketManager()
        mock_ws = AsyncMock()
        manager._connections[_TENANT_ID] = {mock_ws}

        await manager.disconnect(mock_ws, _TENANT_ID)

        assert _TENANT_ID not in manager._connections
