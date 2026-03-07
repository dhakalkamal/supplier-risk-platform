"""Slack alert dispatch via incoming webhook.

Sends a formatted message to a tenant-configured Slack webhook URL.
Only runs when the tenant has slack.enabled = True in their alert rules.

Message format: supplier name + alert type + score delta + link to app.
"""

from __future__ import annotations

import httpx
import structlog

from backend.app.repositories.alert_repository import AlertRecord

log = structlog.get_logger()

_APP_URL = "https://app.supplierrisk.io"

_SEVERITY_COLOUR: dict[str, str] = {
    "critical": "#dc3545",
    "high": "#fd7e14",
    "medium": "#ffc107",
    "low": "#0d6efd",
}


def _build_slack_payload(alert: AlertRecord, supplier_name: str) -> dict[str, object]:
    """Build Slack Block Kit message payload."""
    colour = _SEVERITY_COLOUR.get(alert.severity, "#6c757d")
    delta_text = ""
    if "delta" in alert.metadata:
        delta_text = f"  •  Score Δ: +{alert.metadata['delta']}"
    alert_url = f"{_APP_URL}/alerts"

    return {
        "attachments": [
            {
                "color": colour,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{alert.title}*\n"
                                f"*Supplier:* {supplier_name}  •  "
                                f"*Severity:* {alert.severity.upper()}"
                                f"{delta_text}"
                            ),
                        },
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": alert.message},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "View Alert"},
                                "url": alert_url,
                            }
                        ],
                    },
                ],
            }
        ]
    }


def send_slack_alert(
    alert: AlertRecord,
    supplier_name: str,
    webhook_url: str,
) -> bool:
    """POST alert to Slack webhook URL.

    Returns True on success, False on failure.
    Never raises — caller is a Celery task.
    """
    payload = _build_slack_payload(alert, supplier_name)
    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0)
        success = response.status_code == 200
        log.info(
            "slack_service.sent",
            status_code=response.status_code,
            alert_type=alert.alert_type,
            success=success,
        )
        return success
    except Exception as exc:
        log.error("slack_service.send_failed", error=str(exc), alert_type=alert.alert_type)
        return False
