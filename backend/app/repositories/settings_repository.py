"""Settings repository — alert rules CRUD and user management.

Protocol + InMemory + Postgres pattern (ADR-010).
Alert rules: one row per tenant in alert_rules table (UNIQUE on tenant_id).
Users: tenant-scoped listing and deletion.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import asyncpg
import structlog

from backend.app.models.requests import AlertRulesRequest
from backend.app.models.responses import (
    AlertRulesResponse,
    ChannelsResponse,
    EmailChannelResponse,
    SlackChannelResponse,
    UserResponse,
    WebhookChannelResponse,
)

log = structlog.get_logger()

_DEFAULT_CHANNELS = ChannelsResponse(
    email=EmailChannelResponse(enabled=True, recipients=[]),
    slack=SlackChannelResponse(enabled=False, webhook_url=None, webhook_verified=False),
    webhook=WebhookChannelResponse(enabled=False, url=None, secret=None),
)

_DEFAULT_RULES = AlertRulesResponse(
    score_spike_threshold=15,
    high_risk_threshold=70,
    channels=_DEFAULT_CHANNELS,
    updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _usr_id(raw: Any) -> str:
    return "usr_" + str(raw).replace("-", "")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SettingsRepository(Protocol):
    async def get_alert_rules(self, tenant_id: str) -> AlertRulesResponse:
        """Return the tenant's alert rules. Returns defaults if not yet configured."""
        ...

    async def upsert_alert_rules(
        self,
        tenant_id: str,
        request: AlertRulesRequest,
    ) -> AlertRulesResponse:
        """Create or replace alert rules for the tenant."""
        ...

    async def list_users(
        self,
        tenant_id: str,
        page: int,
        per_page: int,
    ) -> tuple[list[UserResponse], int]:
        """Return paginated users for the tenant."""
        ...

    async def delete_user(self, user_id: str, tenant_id: str) -> None:
        """Remove a user from the tenant. Raises if not found."""
        ...

    async def count_users(self, tenant_id: str) -> int:
        """Return the number of users in the tenant."""
        ...

    async def user_exists_by_email(self, tenant_id: str, email: str) -> bool:
        """True if a user with this email already exists in the tenant."""
        ...


# ---------------------------------------------------------------------------
# InMemory implementation
# ---------------------------------------------------------------------------


class InMemorySettingsRepository:
    def __init__(self) -> None:
        self._rules: dict[str, AlertRulesResponse] = {}
        self._users: dict[str, dict[str, Any]] = {}

    def seed_rules(self, tenant_id: str, rules: AlertRulesResponse) -> None:
        self._rules[tenant_id] = rules

    def seed_user(self, user: dict[str, Any]) -> None:
        self._users[user["user_id"]] = user

    async def get_alert_rules(self, tenant_id: str) -> AlertRulesResponse:
        return self._rules.get(tenant_id, _DEFAULT_RULES)

    async def upsert_alert_rules(
        self,
        tenant_id: str,
        request: AlertRulesRequest,
    ) -> AlertRulesResponse:
        channels = ChannelsResponse(
            email=EmailChannelResponse(
                enabled=request.channels.email.enabled,
                recipients=[str(r) for r in request.channels.email.recipients],
            ),
            slack=SlackChannelResponse(
                enabled=request.channels.slack.enabled,
                webhook_url=request.channels.slack.webhook_url,
                webhook_verified=False,
            ),
            webhook=WebhookChannelResponse(
                enabled=request.channels.webhook.enabled,
                url=request.channels.webhook.url,
                secret=request.channels.webhook.secret,
            ),
        )
        rules = AlertRulesResponse(
            score_spike_threshold=request.score_spike_threshold,
            high_risk_threshold=request.high_risk_threshold,
            channels=channels,
            updated_at=_now(),
        )
        self._rules[tenant_id] = rules
        return rules

    async def list_users(
        self,
        tenant_id: str,
        page: int,
        per_page: int,
    ) -> tuple[list[UserResponse], int]:
        users = [u for u in self._users.values() if u.get("tenant_id") == tenant_id]
        total = len(users)
        start = (page - 1) * per_page
        return [
            UserResponse(
                user_id=u["user_id"],
                email=u["email"],
                role=u["role"],
                created_at=u["created_at"],
                last_active_at=u.get("last_active_at"),
            )
            for u in users[start : start + per_page]
        ], total

    async def delete_user(self, user_id: str, tenant_id: str) -> None:
        user = self._users.get(user_id)
        if user is None or user.get("tenant_id") != tenant_id:
            from backend.app.models.errors import PortfolioSupplierNotFoundError

            raise PortfolioSupplierNotFoundError(user_id)
        del self._users[user_id]

    async def count_users(self, tenant_id: str) -> int:
        return sum(1 for u in self._users.values() if u.get("tenant_id") == tenant_id)

    async def user_exists_by_email(self, tenant_id: str, email: str) -> bool:
        return any(
            u.get("email") == email and u.get("tenant_id") == tenant_id
            for u in self._users.values()
        )


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresSettingsRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_alert_rules(self, tenant_id: str) -> AlertRulesResponse:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM alert_rules WHERE tenant_id = $1::uuid",
                tenant_id,
            )
        if row is None:
            return _DEFAULT_RULES
        return _row_to_rules(row)

    async def upsert_alert_rules(
        self,
        tenant_id: str,
        request: AlertRulesRequest,
    ) -> AlertRulesResponse:
        channels_json = json.dumps(
            {
                "email": {
                    "enabled": request.channels.email.enabled,
                    "recipients": [str(r) for r in request.channels.email.recipients],
                },
                "slack": {
                    "enabled": request.channels.slack.enabled,
                    "webhook_url": request.channels.slack.webhook_url,
                },
                "webhook": {
                    "enabled": request.channels.webhook.enabled,
                    "url": request.channels.webhook.url,
                },
            }
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO alert_rules
                    (tenant_id, score_spike_threshold, high_risk_threshold, channels)
                VALUES ($1::uuid, $2, $3, $4::jsonb)
                ON CONFLICT (tenant_id) DO UPDATE
                    SET score_spike_threshold = EXCLUDED.score_spike_threshold,
                        high_risk_threshold   = EXCLUDED.high_risk_threshold,
                        channels              = EXCLUDED.channels,
                        updated_at            = NOW()
                RETURNING *
                """,
                tenant_id,
                request.score_spike_threshold,
                request.high_risk_threshold,
                channels_json,
            )
        return _row_to_rules(row)

    async def list_users(
        self,
        tenant_id: str,
        page: int,
        per_page: int,
    ) -> tuple[list[UserResponse], int]:
        offset = (page - 1) * per_page
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, email, role, created_at,
                       COUNT(*) OVER() AS total_count
                FROM users
                WHERE tenant_id = $1::uuid
                ORDER BY created_at ASC
                LIMIT $2 OFFSET $3
                """,
                tenant_id,
                per_page,
                offset,
            )
        if not rows:
            return [], 0
        total = rows[0]["total_count"]
        return [
            UserResponse(
                user_id=_usr_id(row["id"]),
                email=row["email"],
                role=row["role"],
                created_at=row["created_at"],
                last_active_at=None,
            )
            for row in rows
        ], total

    async def delete_user(self, user_id: str, tenant_id: str) -> None:
        raw_uuid = user_id.removeprefix("usr_")
        try:
            raw_id = uuid.UUID(raw_uuid)
        except ValueError:
            from backend.app.models.errors import PortfolioSupplierNotFoundError
            raise PortfolioSupplierNotFoundError(user_id)

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM users WHERE id = $1 AND tenant_id = $2::uuid",
                raw_id,
                tenant_id,
            )
        if result == "DELETE 0":
            from backend.app.models.errors import PortfolioSupplierNotFoundError
            raise PortfolioSupplierNotFoundError(user_id)

    async def count_users(self, tenant_id: str) -> int:
        async with self._pool.acquire() as conn:
            result: int = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE tenant_id = $1::uuid",
                tenant_id,
            )
            return result

    async def user_exists_by_email(self, tenant_id: str, email: str) -> bool:
        async with self._pool.acquire() as conn:
            result: int = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE tenant_id = $1::uuid AND email = $2",
                tenant_id,
                email,
            )
            return result > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_rules(row: asyncpg.Record) -> AlertRulesResponse:
    ch_raw = row["channels"]
    if isinstance(ch_raw, str):
        ch_raw = json.loads(ch_raw)
    channels_dict = ch_raw or {}

    email_cfg = channels_dict.get("email", {})
    slack_cfg = channels_dict.get("slack", {})
    webhook_cfg = channels_dict.get("webhook", {})

    return AlertRulesResponse(
        score_spike_threshold=row["score_spike_threshold"],
        high_risk_threshold=row["high_risk_threshold"],
        channels=ChannelsResponse(
            email=EmailChannelResponse(
                enabled=email_cfg.get("enabled", True),
                recipients=email_cfg.get("recipients", []),
            ),
            slack=SlackChannelResponse(
                enabled=slack_cfg.get("enabled", False),
                webhook_url=slack_cfg.get("webhook_url"),
                webhook_verified=slack_cfg.get("webhook_verified", False),
            ),
            webhook=WebhookChannelResponse(
                enabled=webhook_cfg.get("enabled", False),
                url=webhook_cfg.get("url"),
                secret=webhook_cfg.get("secret"),
            ),
        ),
        updated_at=row["updated_at"],
    )
