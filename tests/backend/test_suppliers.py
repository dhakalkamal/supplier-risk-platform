"""Tests for supplier profile, score history, news, and resolve endpoints."""

from __future__ import annotations

from datetime import date, datetime, timezone

from starlette.testclient import TestClient

from backend.app.repositories.score_repository import InMemoryScoreRepository, SupplierScore
from backend.app.repositories.supplier_repository import InMemorySupplierRepository


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TestSupplierProfile:
    def test_get_supplier_returns_200(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        supplier_repo.seed_supplier("sup_abc123", canonical_name="Acme Corp", country="US")
        resp = client.get("/api/v1/suppliers/sup_abc123")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["supplier_id"] == "sup_abc123"
        assert data["canonical_name"] == "Acme Corp"

    def test_get_supplier_returns_404_when_not_found(
        self,
        client: TestClient,
    ) -> None:
        resp = client.get("/api/v1/suppliers/sup_doesnotexist")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SUPPLIER_NOT_FOUND"

    def test_get_supplier_includes_current_score(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
        score_repo: InMemoryScoreRepository,
    ) -> None:
        supplier_repo.seed_supplier("sup_abc123", canonical_name="Acme Corp")
        score_repo.seed_score(
            SupplierScore(
                supplier_id="sup_abc123",
                score=72,
                risk_level="high",
                score_date=date.today(),
                signal_breakdown={},
                model_version="heuristic_v0",
                data_completeness=0.85,
                scored_at=_now(),
            )
        )
        resp = client.get("/api/v1/suppliers/sup_abc123")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["current_score"] is not None
        assert data["current_score"]["score"] == 72
        assert data["current_score"]["risk_level"] == "high"

    def test_get_supplier_no_score_returns_null_current_score(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        supplier_repo.seed_supplier("sup_noscores", canonical_name="No Score Corp")
        resp = client.get("/api/v1/suppliers/sup_noscores")
        assert resp.status_code == 200
        assert resp.json()["data"]["current_score"] is None


class TestScoreHistory:
    def test_score_history_returns_200(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
        score_repo: InMemoryScoreRepository,
    ) -> None:
        supplier_repo.seed_supplier("sup_hist001", canonical_name="History Corp")
        score_repo.seed_score(
            SupplierScore(
                supplier_id="sup_hist001",
                score=45,
                risk_level="medium",
                score_date=date.today(),
                signal_breakdown={},
                model_version="heuristic_v0",
                data_completeness=None,
                scored_at=_now(),
            )
        )
        resp = client.get("/api/v1/suppliers/sup_hist001/score-history?days=30")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["supplier_id"] == "sup_hist001"
        assert data["days_requested"] == 30
        assert isinstance(data["scores"], list)
        assert len(data["scores"]) >= 1

    def test_score_history_404_for_unknown_supplier(
        self,
        client: TestClient,
    ) -> None:
        resp = client.get("/api/v1/suppliers/sup_unknown/score-history")
        assert resp.status_code == 404

    def test_score_history_structure(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
        score_repo: InMemoryScoreRepository,
    ) -> None:
        supplier_repo.seed_supplier("sup_struct01", canonical_name="Struct Corp")
        score_repo.seed_score(
            SupplierScore(
                supplier_id="sup_struct01",
                score=30,
                risk_level="low",
                score_date=date.today(),
                signal_breakdown={},
                model_version="heuristic_v0",
                data_completeness=1.0,
                scored_at=_now(),
            )
        )
        resp = client.get("/api/v1/suppliers/sup_struct01/score-history")
        scores = resp.json()["data"]["scores"]
        assert len(scores) > 0
        item = scores[0]
        assert "date" in item
        assert "score" in item
        assert "risk_level" in item
        assert "model_version" in item


class TestResolveSupplier:
    def test_resolve_returns_200_with_match(
        self,
        client: TestClient,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        from backend.app.models.responses import ResolveSupplierResponse

        mock_result = ResolveSupplierResponse(
            resolved=True,
            supplier_id="sup_abc123",
            canonical_name="Acme Corp",
            country="US",
            confidence=1.0,
            match_method="exact",
            alternatives=[],
        )
        with patch(
            "backend.app.api.v1.routes.suppliers.resolve_supplier",
            AsyncMock(return_value=mock_result),
        ):
            resp = client.post("/api/v1/suppliers/resolve", json={"name": "Acme Corp"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["resolved"] is True
        assert data["supplier_id"] == "sup_abc123"
        assert data["confidence"] == 1.0
        assert data["match_method"] == "exact"

    def test_resolve_returns_200_when_no_match(
        self,
        client: TestClient,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        from backend.app.models.responses import ResolveSupplierResponse

        mock_result = ResolveSupplierResponse(
            resolved=False,
            supplier_id=None,
            canonical_name=None,
            country=None,
            confidence=0.0,
            match_method="no_match",
            alternatives=[],
        )
        with patch(
            "backend.app.api.v1.routes.suppliers.resolve_supplier",
            AsyncMock(return_value=mock_result),
        ):
            resp = client.post(
                "/api/v1/suppliers/resolve",
                json={"name": "Nonexistent Corp XYZ", "country_hint": "DE"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["resolved"] is False
        assert data["supplier_id"] is None
