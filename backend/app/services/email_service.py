"""Email alert dispatch via SendGrid.

In dev/test (email_enabled=False): logs the email content, does not send.
In staging/prod (email_enabled=True): sends via SendGrid API.

Subject format: "🔴 [HIGH] {supplier_name} — {alert_title} | Supplier Risk Platform"
"""

from __future__ import annotations

import structlog

from backend.app.config import get_settings
from backend.app.repositories.alert_repository import AlertRecord

log = structlog.get_logger()

_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}


def _build_subject(alert: AlertRecord, supplier_name: str) -> str:
    emoji = _SEVERITY_EMOJI.get(alert.severity, "🔵")
    return (
        f"{emoji} [{alert.severity.upper()}] {supplier_name} — "
        f"{alert.title} | Supplier Risk Platform"
    )


def _build_plain_body(alert: AlertRecord, supplier_name: str) -> str:
    lines = [
        f"Alert: {alert.title}",
        f"Supplier: {supplier_name}",
        f"Severity: {alert.severity.upper()}",
        f"Type: {alert.alert_type}",
        "",
        alert.message,
        "",
        "Log in to Supplier Risk Platform to investigate:",
        "https://app.supplierrisk.io/alerts",
        "",
        "— Supplier Risk Platform",
    ]
    return "\n".join(lines)


def _build_html_body(alert: AlertRecord, supplier_name: str) -> str:
    emoji = _SEVERITY_EMOJI.get(alert.severity, "🔵")
    return f"""
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
    <h2 style="color: #dc3545;">{emoji} {alert.title}</h2>
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
      <tr>
        <td style="padding: 6px; font-weight: bold; width: 120px;">Supplier</td>
        <td style="padding: 6px;">{supplier_name}</td>
      </tr>
      <tr style="background: #fff;">
        <td style="padding: 6px; font-weight: bold;">Severity</td>
        <td style="padding: 6px;">{alert.severity.upper()}</td>
      </tr>
      <tr>
        <td style="padding: 6px; font-weight: bold;">Alert Type</td>
        <td style="padding: 6px;">{alert.alert_type}</td>
      </tr>
    </table>
    <p style="color: #333;">{alert.message}</p>
    <a href="https://app.supplierrisk.io/alerts"
       style="background: #0d6efd; color: #fff; padding: 10px 20px;
              text-decoration: none; border-radius: 4px; display: inline-block;">
      View Alert
    </a>
  </div>
  <p style="color: #999; font-size: 12px; margin-top: 16px;">
    Supplier Risk Platform · Unsubscribe via Settings
  </p>
</body>
</html>
""".strip()


def send_alert_email(
    alert: AlertRecord,
    supplier_name: str,
    recipients: list[str],
) -> bool:
    """Send an alert email to the given recipients.

    Returns True on success (or skipped in dev mode), False on failure.
    Never raises — caller is a Celery task.
    """
    if not recipients:
        log.info("email_service.no_recipients", alert_type=alert.alert_type)
        return True

    cfg = get_settings()
    subject = _build_subject(alert, supplier_name)
    plain = _build_plain_body(alert, supplier_name)
    html = _build_html_body(alert, supplier_name)

    if not cfg.email_enabled:
        log.info(
            "email_service.dev_mode_skip",
            subject=subject,
            recipients=recipients,
            plain_body=plain,
        )
        return True

    return _send_via_sendgrid(
        cfg.sendgrid_api_key, cfg.email_from, recipients, subject, plain, html
    )


def _send_via_sendgrid(
    api_key: str,
    from_email: str,
    recipients: list[str],
    subject: str,
    plain: str,
    html: str,
) -> bool:  # pragma: no cover
    """Send via SendGrid API. Separated so dev-mode path is fully testable."""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Content, Email, Mail, To

        sg = SendGridAPIClient(api_key)
        message = Mail(
            from_email=Email(from_email),
            to_emails=[To(r) for r in recipients],
            subject=subject,
        )
        message.add_content(Content("text/plain", plain))
        message.add_content(Content("text/html", html))
        response = sg.send(message)
        success = response.status_code in (200, 202)
        log.info(
            "email_service.sent",
            status_code=response.status_code,
            recipients=recipients,
            success=success,
        )
        return success
    except Exception as exc:
        log.error("email_service.send_failed", error=str(exc))
        return False
