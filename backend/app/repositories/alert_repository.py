"""Alert repository — list, filter, and state-managed patch.

Protocol + InMemory + Postgres pattern (ADR-010).
State transition validation lives here, not in route handlers (SESSION_6 rule).
Inject PostgresAlertRepository via FastAPI Depends() — never instantiate directly.

ID convention:
  alerts.id → raw UUID in DB; returned as 'alr_<uuid_no_dashes>' in API
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import asyncpg
import structlog

from backend.app.models.errors import AlertNotFoundError, InvalidStateTransitionError
from backend.app.models.requests import PatchAlertRequest
from backend.app.models.responses import AlertResponse, PatchAlertResponse

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Alert state machine (API_SPEC.md Section 7.4)
# ---------------------------------------------------------------------------

#: Maps current status → list of valid next statuses.
VALID_ALERT_TRANSITIONS: dict[str, list[str]] = {
    "new": ["investigating", "resolved", "dismissed"],
    "investigating": ["resolved", "new"],
    "resolved": ["investigating"],
    "dismissed": ["investigating"],
}


def _validate_transition(current: str, requested: str) -> None:
    """Raise InvalidStateTransitionError if the transition is not permitted."""
    allowed = VALID_ALERT_TRANSITIONS.get(current, [])
    if requested not in allowed:
        raise InvalidStateTransitionError(
            current_status=current,
            requested_status=requested,
            allowed_transitions=allowed,
        )


def _alr_id(raw: Any) -> str:
    """Format a raw UUID as an 'alr_' prefixed alert ID."""
    return "alr_" + str(raw).replace("-", "")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AlertRepository(Protocol):
    """Contract for alert data access."""

    async def list_alerts(
        self,
        tenant_id: str,
        status: str | None,
        severity: str | None,
        supplier_id: str | None,
        alert_type: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[AlertResponse], int]:
        """Return paginated alerts for the tenant, newest first."""
        ...

    async def patch_alert(
        self,
        tenant_id: str,
        alert_id: str,
        request: PatchAlertRequest,
    ) -> PatchAlertResponse:
        """Update alert status and/or note with state-machine validation.

        Raises AlertNotFoundError if alert not found for this tenant.
        Raises InvalidStateTransitionError if status transition is not allowed.
        """
        ...

    async def insert_alert(self, alert: "AlertRecord") -> str:
        """Persist a new alert. Returns the generated alert_id (alr_ prefixed)."""
        ...

    async def has_recent_alert(
        self,
        supplier_id: str,
        alert_type: str,
        tenant_id: str,
        within_hours: int = 24,
    ) -> bool:
        """Return True if a non-dismissed alert of this type exists within window."""
        ...


# ---------------------------------------------------------------------------
# Alert record — internal model used by the alert engine
# ---------------------------------------------------------------------------


class AlertRecord:
    """Internal alert data created by AlertEngine before persistence."""

    __slots__ = (
        "supplier_id",
        "tenant_id",
        "alert_type",
        "severity",
        "title",
        "message",
        "metadata",
        "fired_at",
    )

    def __init__(
        self,
        supplier_id: str,
        tenant_id: str,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        metadata: dict[str, Any],
        fired_at: datetime | None = None,
    ) -> None:
        self.supplier_id = supplier_id
        self.tenant_id = tenant_id
        self.alert_type = alert_type
        self.severity = severity
        self.title = title
        self.message = message
        self.metadata = metadata
        self.fired_at = fired_at or _now()


# ---------------------------------------------------------------------------
# InMemory implementation — for unit tests
# ---------------------------------------------------------------------------


class InMemoryAlertRepository:
    """In-memory alert repository. Pre-populate with seed_alert()."""

    def __init__(self) -> None:
        # alert_id (alr_xxx) → dict of alert fields
        self._alerts: dict[str, dict[str, Any]] = {}
        # supplier_id → canonical_name (for supplier_name in responses)
        self._supplier_names: dict[str, str] = {}

    def seed_alert(self, alert: dict[str, Any]) -> None:
        """Seed a raw alert dict. Must include alert_id (alr_ prefixed)."""
        self._alerts[alert["alert_id"]] = alert

    def seed_supplier_name(self, supplier_id: str, canonical_name: str) -> None:
        self._supplier_names[supplier_id] = canonical_name

    async def list_alerts(
        self,
        tenant_id: str,
        status: str | None,
        severity: str | None,
        supplier_id: str | None,
        alert_type: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[AlertResponse], int]:
        alerts = [
            a for a in self._alerts.values() if a.get("tenant_id") == tenant_id
        ]

        effective_status = None if status == "all" else status
        if effective_status:
            alerts = [a for a in alerts if a.get("status") == effective_status]
        if severity:
            alerts = [a for a in alerts if a.get("severity") == severity]
        if supplier_id:
            alerts = [a for a in alerts if a.get("supplier_id") == supplier_id]
        if alert_type:
            alerts = [a for a in alerts if a.get("alert_type") == alert_type]

        alerts.sort(key=lambda a: a.get("fired_at", _now()), reverse=True)
        total = len(alerts)
        start = (page - 1) * per_page
        page_alerts = alerts[start : start + per_page]

        return [_dict_to_alert_response(a, self._supplier_names) for a in page_alerts], total

    async def patch_alert(
        self,
        tenant_id: str,
        alert_id: str,
        request: PatchAlertRequest,
    ) -> PatchAlertResponse:
        alert = self._alerts.get(alert_id)
        if alert is None or alert.get("tenant_id") != tenant_id:
            raise AlertNotFoundError(alert_id)

        if request.status is not None:
            _validate_transition(alert["status"], request.status)
            alert["status"] = request.status
            if request.status == "resolved":
                alert["resolved_at"] = _now()

        if request.note is not None:
            alert["note"] = request.note

        now = _now()
        return PatchAlertResponse(
            alert_id=alert_id,
            status=alert["status"],
            note=alert.get("note"),
            updated_at=now,
        )

    async def insert_alert(self, alert: AlertRecord) -> str:
        alert_id = _alr_id(uuid.uuid4())
        self._alerts[alert_id] = {
            "alert_id": alert_id,
            "supplier_id": alert.supplier_id,
            "tenant_id": alert.tenant_id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "title": alert.title,
            "message": alert.message,
            "metadata": alert.metadata,
            "status": "new",
            "note": None,
            "fired_at": alert.fired_at,
            "read_at": None,
            "resolved_at": None,
        }
        return alert_id

    async def has_recent_alert(
        self,
        supplier_id: str,
        alert_type: str,
        tenant_id: str,
        within_hours: int = 24,
    ) -> bool:
        from datetime import timedelta

        cutoff = _now() - timedelta(hours=within_hours)
        return any(
            a.get("supplier_id") == supplier_id
            and a.get("alert_type") == alert_type
            and a.get("tenant_id") == tenant_id
            and a.get("status") != "dismissed"
            and a.get("fired_at", _now()) > cutoff
            for a in self._alerts.values()
        )


# ---------------------------------------------------------------------------
# Postgres implementation — production
# ---------------------------------------------------------------------------


class PostgresAlertRepository:
    """Production alert repository backed by asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_alerts(
        self,
        tenant_id: str,
        status: str | None,
        severity: str | None,
        supplier_id: str | None,
        alert_type: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[AlertResponse], int]:
        conditions = ["a.tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        n = 2

        if status and status != "all":
            conditions.append(f"a.status = ${n}")
            args.append(status)
            n += 1
        if severity:
            conditions.append(f"a.severity = ${n}")
            args.append(severity)
            n += 1
        if supplier_id:
            conditions.append(f"a.supplier_id = ${n}")
            args.append(supplier_id)
            n += 1
        if alert_type:
            conditions.append(f"a.alert_type = ${n}")
            args.append(alert_type)
            n += 1

        where = " AND ".join(conditions)
        offset = (page - 1) * per_page

        query = f"""
            SELECT
                a.id          AS alert_uuid,
                a.supplier_id,
                s.canonical_name AS supplier_name,
                a.alert_type,
                a.severity,
                a.title,
                a.message,
                a.metadata,
                a.status,
                a.note,
                a.fired_at,
                a.read_at,
                a.resolved_at,
                COUNT(*) OVER() AS total_count
            FROM alerts a
            JOIN suppliers s ON s.id = a.supplier_id
            WHERE {where}
            ORDER BY a.fired_at DESC
            LIMIT ${n} OFFSET ${n + 1}
        """
        args.extend([per_page, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)

        if not rows:
            return [], 0

        total = rows[0]["total_count"]
        return [_record_to_alert_response(row) for row in rows], total

    async def patch_alert(
        self,
        tenant_id: str,
        alert_id: str,
        request: PatchAlertRequest,
    ) -> PatchAlertResponse:
        # Strip 'alr_' prefix to recover the UUID
        raw_uuid = alert_id.removeprefix("alr_")
        try:
            raw_id = uuid.UUID(raw_uuid)
        except ValueError:
            raise AlertNotFoundError(alert_id)

        async with self._pool.acquire() as conn:
            current = await conn.fetchrow(
                "SELECT status, note FROM alerts WHERE id = $1 AND tenant_id = $2::uuid",
                raw_id,
                tenant_id,
            )
            if current is None:
                raise AlertNotFoundError(alert_id)

            if request.status is not None:
                _validate_transition(current["status"], request.status)

            updates: list[str] = []
            args: list[Any] = []
            n = 1

            if request.status is not None:
                updates.append(f"status = ${n}")
                args.append(request.status)
                n += 1
                if request.status == "resolved":
                    updates.append(f"resolved_at = ${n}")
                    args.append(_now())
                    n += 1

            if request.note is not None:
                updates.append(f"note = ${n}")
                args.append(request.note)
                n += 1

            note = request.note if request.note is not None else current["note"]
            new_status = request.status if request.status is not None else current["status"]

            if updates:
                args.extend([raw_id, uuid.UUID(tenant_id)])
                await conn.execute(
                    f"UPDATE alerts SET {', '.join(updates)} "
                    f"WHERE id = ${n} AND tenant_id = ${n + 1}",
                    *args,
                )

        now = _now()
        return PatchAlertResponse(
            alert_id=alert_id,
            status=new_status,
            note=note,
            updated_at=now,
        )

    async def insert_alert(self, alert: AlertRecord) -> str:  # pragma: no cover
        import json as _json

        new_id = uuid.uuid4()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO alerts
                    (id, supplier_id, tenant_id, alert_type, severity,
                     title, message, metadata, status, fired_at)
                VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8::jsonb, 'new', $9)
                """,
                new_id,
                alert.supplier_id,
                alert.tenant_id,
                alert.alert_type,
                alert.severity,
                alert.title,
                alert.message,
                _json.dumps(alert.metadata),
                alert.fired_at,
            )
        return _alr_id(new_id)

    async def has_recent_alert(  # pragma: no cover
        self,
        supplier_id: str,
        alert_type: str,
        tenant_id: str,
        within_hours: int = 24,
    ) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM alerts
                WHERE supplier_id = $1
                  AND alert_type = $2
                  AND tenant_id = $3::uuid
                  AND status != 'dismissed'
                  AND fired_at > NOW() - ($4 || ' hours')::interval
                LIMIT 1
                """,
                supplier_id,
                alert_type,
                tenant_id,
                str(within_hours),
            )
        return row is not None


# ---------------------------------------------------------------------------
# Row → response model helpers
# ---------------------------------------------------------------------------


def _dict_to_alert_response(
    alert: dict[str, Any],
    supplier_names: dict[str, str],
) -> AlertResponse:
    return AlertResponse(
        alert_id=alert["alert_id"],
        supplier_id=alert["supplier_id"],
        supplier_name=supplier_names.get(alert["supplier_id"], "Unknown"),
        alert_type=alert["alert_type"],
        severity=alert["severity"],
        title=alert["title"],
        message=alert["message"],
        metadata=alert.get("metadata", {}),
        status=alert["status"],
        note=alert.get("note"),
        fired_at=alert["fired_at"],
        read_at=alert.get("read_at"),
        resolved_at=alert.get("resolved_at"),
    )


def _record_to_alert_response(row: asyncpg.Record) -> AlertResponse:
    import json as _json

    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = _json.loads(metadata)

    return AlertResponse(
        alert_id=_alr_id(row["alert_uuid"]),
        supplier_id=row["supplier_id"],
        supplier_name=row["supplier_name"],
        alert_type=row["alert_type"],
        severity=row["severity"],
        title=row["title"],
        message=row["message"],
        metadata=metadata or {},
        status=row["status"],
        note=row["note"],
        fired_at=row["fired_at"],
        read_at=row["read_at"],
        resolved_at=row["resolved_at"],
    )
