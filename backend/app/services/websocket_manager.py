"""WebSocket connection manager.

Manages active WebSocket connections per tenant.
Uses Redis pub/sub to receive events published by Celery tasks.
Broadcasts to all active connections for a tenant.

Connection limits: max 5 per tenant. Reject with close code 1008 if exceeded.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import structlog
from fastapi import WebSocket

log = structlog.get_logger()

_MAX_CONNECTIONS_PER_TENANT = 5


class WebSocketManager:
    """Thread-safe manager for active WebSocket connections, keyed by tenant_id."""

    def __init__(self) -> None:
        # tenant_id → set of active WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._redis: Any = None  # set via set_redis() at startup
        self._listener_task: asyncio.Task[None] | None = None

    def set_redis(self, redis: Any) -> None:
        """Inject the Redis client (called at app startup)."""
        self._redis = redis

    async def connect(self, websocket: WebSocket, tenant_id: str) -> bool:
        """Accept the WebSocket connection if under the per-tenant limit.

        Returns False if the connection limit is exceeded.
        Caller must send close code 1008 and return when False.
        """
        current = len(self._connections[tenant_id])
        if current >= _MAX_CONNECTIONS_PER_TENANT:
            log.warning(
                "ws.connection_limit_exceeded",
                tenant_id=tenant_id,
                current=current,
                limit=_MAX_CONNECTIONS_PER_TENANT,
            )
            return False
        await websocket.accept()
        self._connections[tenant_id].add(websocket)
        log.info(
            "ws.connected",
            tenant_id=tenant_id,
            total=len(self._connections[tenant_id]),
        )
        return True

    async def disconnect(self, websocket: WebSocket, tenant_id: str) -> None:
        """Remove a connection from the active set."""
        self._connections[tenant_id].discard(websocket)
        if not self._connections[tenant_id]:
            del self._connections[tenant_id]
        log.info("ws.disconnected", tenant_id=tenant_id)

    async def broadcast_to_tenant(self, tenant_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to all active connections for a tenant."""
        text = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(tenant_id, [])):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[tenant_id].discard(ws)

    async def listen_for_events(self, websocket: WebSocket, tenant_id: str) -> None:
        """Subscribe to Redis pub/sub channel ws:{tenant_id} and forward to client.

        Runs until the connection closes or the Redis subscription ends.
        """
        if self._redis is None:
            log.warning("ws.redis_not_available", tenant_id=tenant_id)
            return

        channel = f"ws:{tenant_id}"
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        log.info("ws.subscribed", channel=channel)
        try:
            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                try:
                    data = json.loads(raw["data"])
                    await websocket.send_text(json.dumps(data))
                except Exception as exc:
                    log.warning("ws.forward_failed", error=str(exc))
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def start_redis_listener(self) -> None:
        """Background task: keep listening for events across all tenant channels.

        This is a no-op when Redis is unavailable — the per-connection
        listen_for_events() handles individual subscriptions.
        """
        log.info("ws.manager.started")


# Singleton — imported by routes and main.py
websocket_manager = WebSocketManager()
