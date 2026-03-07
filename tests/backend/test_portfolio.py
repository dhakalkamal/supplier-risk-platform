"""Tests for portfolio endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from backend.app.repositories.score_repository import InMemoryScoreRepository, SupplierScore
from backend.app.repositories.supplier_repository import (
    InMemorySupplierRepository,
    PortfolioSupplierRecord,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed_supplier(repo: InMemorySupplierRepository, supplier_id: str = "sup_abc123") -> None:
    repo.seed_supplier(supplier_id, canonical_name="Acme Corp", country="US")


def _seed_portfolio(
    supplier_repo: InMemorySupplierRepository, supplier_id: str = "sup_abc123"
) -> str:
    pf_id = "pf_00000000000000000000000000000001"
    now = _now()
    supplier_repo.seed_portfolio(
        PortfolioSupplierRecord(
            portfolio_supplier_id=pf_id,
            supplier_id=supplier_id,
            tenant_id="10000000-0000-0000-0000-000000000001",
            canonical_name="Acme Corp",
            custom_name=None,
            internal_id="INT-001",
            tags=["critical"],
            resolution_confidence=None,
            resolution_method=None,
            added_at=now,
            updated_at=now,
        )
    )
    return pf_id


class TestPortfolioSummary:
    def test_summary_returns_200(
        self, client: TestClient, score_repo: InMemoryScoreRepository
    ) -> None:
        resp = client.get("/api/v1/portfolio/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert "total_suppliers" in data
        assert "high_risk_count" in data
        assert "average_portfolio_score" in data

    def test_summary_structure(
        self, client: TestClient, score_repo: InMemoryScoreRepository
    ) -> None:
        resp = client.get("/api/v1/portfolio/summary")
        data = resp.json()["data"]
        required_fields = [
            "total_suppliers",
            "high_risk_count",
            "medium_risk_count",
            "low_risk_count",
            "unread_alerts_count",
            "average_portfolio_score",
            "score_trend_7d",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"


class TestPortfolioSuppliersList:
    def test_list_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/v1/portfolio/suppliers")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body

    def test_list_pagination_meta(self, client: TestClient) -> None:
        resp = client.get("/api/v1/portfolio/suppliers?page=1&per_page=10")
        meta = resp.json()["meta"]
        assert meta["page"] == 1
        assert meta["per_page"] == 10
        assert "total" in meta
        assert "total_pages" in meta

    def test_list_returns_seeded_supplier(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        _seed_supplier(supplier_repo)
        _seed_portfolio(supplier_repo)
        resp = client.get("/api/v1/portfolio/suppliers")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["canonical_name"] == "Acme Corp"


class TestAddSupplier:
    def test_add_by_supplier_id_returns_201(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        _seed_supplier(supplier_repo)
        resp = client.post(
            "/api/v1/portfolio/suppliers",
            json={"supplier_id": "sup_abc123"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["supplier_id"] == "sup_abc123"
        assert data["canonical_name"] == "Acme Corp"
        assert "portfolio_supplier_id" in data

    def test_add_returns_409_when_already_in_portfolio(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        _seed_supplier(supplier_repo)
        _seed_portfolio(supplier_repo)
        resp = client.post(
            "/api/v1/portfolio/suppliers",
            json={"supplier_id": "sup_abc123"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SUPPLIER_ALREADY_IN_PORTFOLIO"

    def test_add_returns_429_when_plan_limit_reached(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        # Growth plan limit = 100. Seed 100 suppliers.
        for i in range(100):
            sid = f"sup_{i:06d}"
            supplier_repo.seed_supplier(sid, canonical_name=f"Supplier {i}")
            now = _now()
            pf_id = f"pf_{i:032x}"
            supplier_repo.seed_portfolio(
                PortfolioSupplierRecord(
                    portfolio_supplier_id=pf_id,
                    supplier_id=sid,
                    tenant_id="10000000-0000-0000-0000-000000000001",
                    canonical_name=f"Supplier {i}",
                    custom_name=None,
                    internal_id=None,
                    tags=[],
                    resolution_confidence=None,
                    resolution_method=None,
                    added_at=now,
                    updated_at=now,
                )
            )
        # Add one more supplier to exceed limit
        supplier_repo.seed_supplier("sup_new001", canonical_name="New Supplier")
        resp = client.post(
            "/api/v1/portfolio/suppliers",
            json={"supplier_id": "sup_new001"},
        )
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "PLAN_LIMIT_EXCEEDED"

    def test_add_unknown_supplier_returns_404(
        self,
        client: TestClient,
    ) -> None:
        resp = client.post(
            "/api/v1/portfolio/suppliers",
            json={"supplier_id": "sup_unknown"},
        )
        assert resp.status_code == 404


class TestDeleteSupplier:
    def test_delete_returns_204(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        _seed_supplier(supplier_repo)
        pf_id = _seed_portfolio(supplier_repo)
        resp = client.delete(f"/api/v1/portfolio/suppliers/{pf_id}")
        assert resp.status_code == 204

    def test_delete_unknown_returns_404(
        self,
        client: TestClient,
    ) -> None:
        resp = client.delete("/api/v1/portfolio/suppliers/pf_doesnotexist000000000")
        assert resp.status_code == 404


class TestPatchSupplier:
    def test_patch_updates_fields(
        self,
        client: TestClient,
        supplier_repo: InMemorySupplierRepository,
    ) -> None:
        _seed_supplier(supplier_repo)
        pf_id = _seed_portfolio(supplier_repo)
        resp = client.patch(
            f"/api/v1/portfolio/suppliers/{pf_id}",
            json={"custom_name": "ACME Updated", "tags": ["tier-1"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["custom_name"] == "ACME Updated"
        assert data["tags"] == ["tier-1"]
