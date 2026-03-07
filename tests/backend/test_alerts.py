"""Tests for alert list and patch endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from backend.app.repositories.alert_repository import InMemoryAlertRepository


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed_alert(repo: InMemoryAlertRepository, alert_id: str = "alr_00000001", status: str = "new") -> None:
    repo.seed_supplier_name("sup_abc123", "Acme Corp")
    repo.seed_alert(
        {
            "alert_id": alert_id,
            "supplier_id": "sup_abc123",
            "alert_type": "score_spike",
            "severity": "high",
            "title": "Score spike detected",
            "message": "Score increased by 20 points",
            "metadata": {},
            "status": status,
            "note": None,
            "fired_at": _now(),
            "read_at": None,
            "resolved_at": None,
            "tenant_id": "10000000-0000-0000-0000-000000000001",
        }
    )


class TestListAlerts:
    def test_list_returns_200(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body

    def test_list_returns_seeded_alerts(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo)
        resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["alert_id"] == "alr_00000001"

    def test_list_filters_by_status(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo, "alr_00000001", status="new")
        _seed_alert(alert_repo, "alr_00000002", status="resolved")
        resp = client.get("/api/v1/alerts?status=new")
        data = resp.json()["data"]
        assert all(a["status"] == "new" for a in data)

    def test_list_all_status_returns_all(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo, "alr_00000001", status="new")
        _seed_alert(alert_repo, "alr_00000002", status="resolved")
        resp = client.get("/api/v1/alerts?status=all")
        data = resp.json()["data"]
        assert len(data) == 2


class TestPatchAlert:
    def test_valid_transition_succeeds(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo, "alr_00000001", status="new")
        resp = client.patch(
            "/api/v1/alerts/alr_00000001",
            json={"status": "investigating"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "investigating"
        assert data["alert_id"] == "alr_00000001"

    def test_invalid_transition_returns_422(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo, "alr_00000001", status="resolved")
        resp = client.patch(
            "/api/v1/alerts/alr_00000001",
            json={"status": "dismissed"},
        )
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "INVALID_STATE_TRANSITION"
        assert "allowed_transitions" in error["details"]

    def test_viewer_cannot_change_status(
        self, viewer_client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo, "alr_00000001", status="new")
        resp = viewer_client.patch(
            "/api/v1/alerts/alr_00000001",
            json={"status": "investigating"},
        )
        assert resp.status_code == 403

    def test_note_update_without_status_succeeds(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        _seed_alert(alert_repo, "alr_00000001", status="investigating")
        resp = client.patch(
            "/api/v1/alerts/alr_00000001",
            json={"note": "Looking into this now"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["note"] == "Looking into this now"

    def test_patch_unknown_alert_returns_404(
        self, client: TestClient, alert_repo: InMemoryAlertRepository
    ) -> None:
        resp = client.patch(
            "/api/v1/alerts/alr_notexist000",
            json={"status": "resolved"},
        )
        assert resp.status_code == 404
