"""WebSocket endpoint for real-time alert and score update streaming.

Auth: JWT in query param ?token=...
On auth failure: close with code 1008 before upgrade.

Message types sent to client:
    alert.fired     — new alert created
    score.updated   — supplier score changed
    ping            — heartbeat every 30 seconds

Client must respond to ping with pong.
Connection closed after 5 minutes of no pong response.
Max 5 concurrent connections per tenant.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from backend.app.config import get_settings
from backend.app.dependencies import TenantContext, _decode_jwt, _fetch_jwks
from backend.app.services.websocket_manager import websocket_manager

log = structlog.get_logger()

router = APIRouter(tags=["websocket"])

_HEARTBEAT_INTERVAL = 30  # seconds between pings
_PONG_TIMEOUT = 300  # 5 minutes — close if no pong received


async def _authenticate_ws(token: str) -> TenantContext | None:
    """Validate JWT from query param. Returns TenantContext or None on failure."""
    settings = get_settings()
    if not settings.auth0_domain:
        # Dev bypass — same as HTTP auth
        return TenantContext(
            tenant_id="dev-tenant-00000000-0000-0000-0000-000000000000",
            user_id="dev-user-00000000-0000-0000-0000-000000000000",
            role="admin",
            plan="enterprise",
            email="dev@localhost",
        )
    try:
        jwks = await _fetch_jwks(settings)
        payload = _decode_jwt(token, jwks, settings)
        required = ("tenant_id", "role", "plan")
        if any(c not in payload for c in required):
            return None
        return TenantContext(
            tenant_id=payload["tenant_id"],
            user_id=payload["sub"],
            role=payload["role"],
            plan=payload["plan"],
            email=payload.get("email", ""),
        )
    except Exception as exc:
        log.warning("ws.auth_failed", error=str(exc))
        return None


async def _run_heartbeat(websocket: WebSocket) -> None:
    """Send a ping every 30s. Track last pong; close after 5 min silence."""
    last_pong = asyncio.get_event_loop().time()

    async def _receive_pong() -> None:
        nonlocal last_pong
        while True:
            try:
                msg = await websocket.receive_text()
                if msg == "pong":
                    last_pong = asyncio.get_event_loop().time()
            except Exception:
                return

    pong_task = asyncio.create_task(_receive_pong())
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            elapsed = asyncio.get_event_loop().time() - last_pong
            if elapsed > _PONG_TIMEOUT:
                log.info("ws.pong_timeout_close")
                await websocket.close(code=1001)
                break
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break
    finally:
        pong_task.cancel()


@router.websocket("/ws/alerts")
async def websocket_alerts(
    websocket: WebSocket,
    token: str = "",
) -> None:
    """Real-time alert and score update stream.

    Connect with: ws://host/api/v1/ws/alerts?token=<JWT>
    """
    # Authenticate before accepting the connection
    if not token:
        await websocket.close(code=1008)
        return

    tenant = await _authenticate_ws(token)
    if tenant is None:
        await websocket.close(code=1008)
        return

    # Enforce per-tenant connection limit
    accepted = await websocket_manager.connect(websocket, tenant.tenant_id)
    if not accepted:
        await websocket.close(code=1008)
        return

    log.info("ws.session_started", tenant_id=tenant.tenant_id, user_id=tenant.user_id)
    try:
        heartbeat_task = asyncio.create_task(_run_heartbeat(websocket))
        events_task = asyncio.create_task(
            websocket_manager.listen_for_events(websocket, tenant.tenant_id)
        )
        # Run until either task ends (disconnect or error)
        done, pending = await asyncio.wait(
            [heartbeat_task, events_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        log.info("ws.client_disconnected", tenant_id=tenant.tenant_id)
    except Exception as exc:
        log.error("ws.session_error", tenant_id=tenant.tenant_id, error=str(exc))
    finally:
        await websocket_manager.disconnect(websocket, tenant.tenant_id)
        log.info("ws.session_ended", tenant_id=tenant.tenant_id)
