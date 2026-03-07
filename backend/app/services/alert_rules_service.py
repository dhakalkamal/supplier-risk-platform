"""Alert rules service — tenant fanout for the Kafka scores consumer.

Provides a query to find all tenants monitoring a given supplier,
along with each tenant's configured alert rules.
"""

from __future__ import annotations

import asyncpg
import structlog

from backend.app.models.responses import (
    AlertRulesResponse,
    ChannelsResponse,
    EmailChannelResponse,
    SlackChannelResponse,
    WebhookChannelResponse,
)

log = structlog.get_logger()

_DEFAULT_CHANNELS = ChannelsResponse(
    email=EmailChannelResponse(enabled=True, recipients=[]),
    slack=SlackChannelResponse(enabled=False, webhook_url=None, webhook_verified=False),
    webhook=WebhookChannelResponse(enabled=False, url=None, secret=None),
)


async def get_tenants_monitoring_supplier(
    supplier_id: str,
    pool: asyncpg.Pool,
) -> list[tuple[str, AlertRulesResponse]]:
    """Return (tenant_id, alert_rules) for all tenants monitoring this supplier.

    Used by the Kafka consumer to fan out alert evaluation after a score update.
    Tenants with no configured rules receive the platform defaults.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                ps.tenant_id::text,
                ar.score_spike_threshold,
                ar.high_risk_threshold,
                ar.channels
            FROM portfolio_suppliers ps
            LEFT JOIN alert_rules ar ON ar.tenant_id = ps.tenant_id
            WHERE ps.supplier_id = $1
              AND ps.removed_at IS NULL
            """,
            supplier_id,
        )

    results: list[tuple[str, AlertRulesResponse]] = []
    for row in rows:
        rules = _row_to_alert_rules(row)
        results.append((row["tenant_id"], rules))

    log.debug(
        "alert_rules_service.tenants_found",
        supplier_id=supplier_id,
        tenant_count=len(results),
    )
    return results


def _row_to_alert_rules(row: asyncpg.Record) -> AlertRulesResponse:  # pragma: no cover
    """Convert a DB row to AlertRulesResponse, falling back to defaults."""
    import json as _json
    from datetime import datetime, timezone

    if row["score_spike_threshold"] is None:
        return AlertRulesResponse(
            score_spike_threshold=15,
            high_risk_threshold=70,
            channels=_DEFAULT_CHANNELS,
            updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

    raw_channels = row["channels"]
    if isinstance(raw_channels, str):
        raw_channels = _json.loads(raw_channels)

    channels_data = raw_channels or {}
    email_data = channels_data.get("email", {})
    slack_data = channels_data.get("slack", {})
    webhook_data = channels_data.get("webhook", {})

    return AlertRulesResponse(
        score_spike_threshold=row["score_spike_threshold"],
        high_risk_threshold=row["high_risk_threshold"],
        channels=ChannelsResponse(
            email=EmailChannelResponse(
                enabled=email_data.get("enabled", True),
                recipients=email_data.get("recipients", []),
            ),
            slack=SlackChannelResponse(
                enabled=slack_data.get("enabled", False),
                webhook_url=slack_data.get("webhook_url"),
                webhook_verified=slack_data.get("webhook_verified", False),
            ),
            webhook=WebhookChannelResponse(
                enabled=webhook_data.get("enabled", False),
                url=webhook_data.get("url"),
                secret=webhook_data.get("secret"),
            ),
        ),
        updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
