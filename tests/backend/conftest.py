"""Shared fixtures for backend API tests.

All tests use InMemory repositories and bypass Auth0 via dependency_overrides.
No real Postgres, Redis, or Kafka required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.app.dependencies import (
    TenantContext,
    get_alert_repository,
    get_current_tenant,
    get_db_pool,
    get_news_repository,
    get_score_repository,
    get_settings_repository,
    get_supplier_repository,
)
from backend.app.main import create_app
from backend.app.repositories.alert_repository import InMemoryAlertRepository
from backend.app.repositories.news_repository import InMemoryNewsRepository
from backend.app.repositories.score_repository import InMemoryScoreRepository
from backend.app.repositories.settings_repository import InMemorySettingsRepository
from backend.app.repositories.supplier_repository import InMemorySupplierRepository

# ---------------------------------------------------------------------------
# Canonical test tenants
# ---------------------------------------------------------------------------

ADMIN_TENANT = TenantContext(
    tenant_id="10000000-0000-0000-0000-000000000001",
    user_id="usr_adminuser001",
    role="admin",
    plan="growth",
    email="admin@test.example",
)

VIEWER_TENANT = TenantContext(
    tenant_id="10000000-0000-0000-0000-000000000001",
    user_id="usr_vieweruser1",
    role="viewer",
    plan="growth",
    email="viewer@test.example",
)


# ---------------------------------------------------------------------------
# Repository fixtures — seeded with test data by individual test files
# ---------------------------------------------------------------------------


@pytest.fixture
def supplier_repo() -> InMemorySupplierRepository:
    return InMemorySupplierRepository()


@pytest.fixture
def score_repo() -> InMemoryScoreRepository:
    return InMemoryScoreRepository()


@pytest.fixture
def alert_repo() -> InMemoryAlertRepository:
    return InMemoryAlertRepository()


@pytest.fixture
def news_repo() -> InMemoryNewsRepository:
    return InMemoryNewsRepository()


@pytest.fixture
def settings_repo() -> InMemorySettingsRepository:
    return InMemorySettingsRepository()


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_app(
    tenant: TenantContext,
    supplier_repo: InMemorySupplierRepository,
    score_repo: InMemoryScoreRepository,
    alert_repo: InMemoryAlertRepository,
    news_repo: InMemoryNewsRepository,
    settings_repo: InMemorySettingsRepository,
) -> Any:
    """Create a FastAPI test app with all dependencies overridden."""
    app = create_app()

    # Override auth — return the given tenant without hitting Auth0
    app.dependency_overrides[get_current_tenant] = lambda: tenant

    # Override db pool — return a MagicMock so routes using get_db_pool directly
    # (e.g. /suppliers/resolve) don't fail with "pool not initialised"
    app.dependency_overrides[get_db_pool] = lambda: MagicMock()

    # Override repositories — use in-memory impls
    app.dependency_overrides[get_supplier_repository] = lambda: supplier_repo
    app.dependency_overrides[get_score_repository] = lambda: score_repo
    app.dependency_overrides[get_alert_repository] = lambda: alert_repo
    app.dependency_overrides[get_news_repository] = lambda: news_repo
    app.dependency_overrides[get_settings_repository] = lambda: settings_repo

    # Skip startup/shutdown — no real Postgres or Redis in unit tests
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    app.state.redis = None

    return app


# ---------------------------------------------------------------------------
# TestClient fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(
    supplier_repo: InMemorySupplierRepository,
    score_repo: InMemoryScoreRepository,
    alert_repo: InMemoryAlertRepository,
    news_repo: InMemoryNewsRepository,
    settings_repo: InMemorySettingsRepository,
) -> Any:
    """Admin-role TestClient with all repositories overridden."""
    app = _make_app(
        ADMIN_TENANT, supplier_repo, score_repo, alert_repo, news_repo, settings_repo
    )
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def viewer_client(
    supplier_repo: InMemorySupplierRepository,
    score_repo: InMemoryScoreRepository,
    alert_repo: InMemoryAlertRepository,
    news_repo: InMemoryNewsRepository,
    settings_repo: InMemorySettingsRepository,
) -> Any:
    """Viewer-role TestClient with all repositories overridden."""
    app = _make_app(
        VIEWER_TENANT, supplier_repo, score_repo, alert_repo, news_repo, settings_repo
    )
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
