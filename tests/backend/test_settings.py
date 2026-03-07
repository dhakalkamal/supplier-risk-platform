"""Tests for settings — alert rules and user management endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from starlette.testclient import TestClient

from backend.app.repositories.settings_repository import InMemorySettingsRepository


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


_DEFAULT_RULES_BODY = {
    "score_spike_threshold": 15,
    "high_risk_threshold": 70,
    "channels": {
        "email": {"enabled": True, "recipients": []},
        "slack": {"enabled": False, "webhook_url": None},
        "webhook": {"enabled": False, "url": None, "secret": None},
    },
}


class TestAlertRules:
    def test_get_alert_rules_returns_200(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        resp = client.get("/api/v1/settings/alert-rules")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "score_spike_threshold" in data
        assert "high_risk_threshold" in data
        assert "channels" in data

    def test_put_alert_rules_returns_200(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        body = {
            "score_spike_threshold": 20,
            "high_risk_threshold": 75,
            "channels": {
                "email": {"enabled": True, "recipients": ["ops@example.com"]},
                "slack": {"enabled": False},
                "webhook": {"enabled": False},
            },
        }
        resp = client.put("/api/v1/settings/alert-rules", json=body)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["score_spike_threshold"] == 20
        assert data["high_risk_threshold"] == 75

    def test_put_alert_rules_viewer_returns_403(
        self, viewer_client: TestClient
    ) -> None:
        resp = viewer_client.put("/api/v1/settings/alert-rules", json=_DEFAULT_RULES_BODY)
        assert resp.status_code == 403


class TestUsers:
    def test_list_users_returns_200(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        resp = client.get("/api/v1/settings/users")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body

    def test_list_users_viewer_returns_403(
        self, viewer_client: TestClient
    ) -> None:
        resp = viewer_client.get("/api/v1/settings/users")
        assert resp.status_code == 403

    def test_invite_user_returns_201(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        resp = client.post(
            "/api/v1/settings/users/invite",
            json={"email": "newuser@example.com", "role": "viewer"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["email"] == "newuser@example.com"
        assert data["role"] == "viewer"
        assert "invite_id" in data
        assert "expires_at" in data

    def test_invite_duplicate_email_returns_409(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        settings_repo.seed_user(
            {
                "user_id": "usr_existing001",
                "email": "existing@example.com",
                "role": "viewer",
                "tenant_id": "10000000-0000-0000-0000-000000000001",
                "created_at": _now(),
            }
        )
        resp = client.post(
            "/api/v1/settings/users/invite",
            json={"email": "existing@example.com", "role": "admin"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "USER_ALREADY_EXISTS"

    def test_delete_self_returns_403(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        # ADMIN_TENANT.user_id == "usr_adminuser001"
        resp = client.delete("/api/v1/settings/users/usr_adminuser001")
        assert resp.status_code == 403

    def test_delete_other_user_returns_204(
        self, client: TestClient, settings_repo: InMemorySettingsRepository
    ) -> None:
        settings_repo.seed_user(
            {
                "user_id": "usr_otheruser01",
                "email": "other@example.com",
                "role": "viewer",
                "tenant_id": "10000000-0000-0000-0000-000000000001",
                "created_at": _now(),
            }
        )
        resp = client.delete("/api/v1/settings/users/usr_otheruser01")
        assert resp.status_code == 204
