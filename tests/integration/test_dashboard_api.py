"""Integration tests for GET /api/v1/dashboard/metrics API endpoint."""

import pytest
from datetime import datetime, timezone
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.dashboard import router as dashboard_router
from src.lib.config import settings
from src.models.base import Base, create_db_engine, get_db
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel
from src.services.dashboard.aggregator import (
    update_domain_metric_cache,
    update_historical_snapshot,
)


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def test_session_token(test_db):
    """Fixture creating tenant, user, and active session records, returning the session ID string."""
    tenant = TenantModel(name="Test Corporate Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="executive@example.com",
        password_hash="hashed_secret",
        full_name="Executive User",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc).replace(year=2099),
    )
    test_db.add(session_record)
    test_db.commit()

    return session_record.id


@pytest.fixture
def client(test_db):
    """Fixture providing a FastAPI TestClient with database session dependency overridden."""
    app = FastAPI()
    app.include_router(dashboard_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_get_dashboard_metrics_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.get("/api/v1/dashboard/metrics")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_dashboard_metrics_authenticated_success(client, test_db, test_session_token):
    """Verify authenticated user receives 200 OK with aggregated domain metrics."""
    # Seed cache and baseline snapshot data
    update_domain_metric_cache(
        db_session=test_db,
        domain_name="sales",
        metric_key="pipeline_value",
        current_value=120000.0,
        unit_type="currency",
    )
    update_historical_snapshot(
        db_session=test_db,
        domain_name="sales",
        metric_key="pipeline_value",
        baseline_period="prior_month",
        snapshot_value=100000.0,
    )

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/dashboard/metrics", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "last_updated_at" in data
    assert "metrics" in data
    assert len(data["metrics"]) > 0

    # Locate sales domain
    sales_domain = next((d for d in data["metrics"] if d["domain_name"] == "sales"), None)
    assert sales_domain is not None
    assert sales_domain["status"] == "active"
    assert len(sales_domain["kpis"]) == 1

    kpi = sales_domain["kpis"][0]
    assert kpi["key"] == "pipeline_value"
    assert kpi["value"] == 120000.0
    assert kpi["trend_direction"] == "up"
    assert kpi["trend_percentage"] == 20.0


def test_get_dashboard_metrics_rbac_pruning(client, test_db, test_session_token):
    """Verify user with role restrictions receives metrics pruned to authorized domains."""
    for domain in ["sales", "leads", "support", "finance"]:
        update_domain_metric_cache(
            db_session=test_db,
            domain_name=domain,
            metric_key=f"{domain}_metric",
            current_value=100.0,
            unit_type="count",
        )

    headers = {
        "Authorization": f"Bearer {test_session_token}",
        "X-User-Roles": "sales_rep",
    }
    response = client.get("/api/v1/dashboard/metrics", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    returned_domains = [d["domain_name"] for d in data["metrics"]]
    assert returned_domains == ["sales"]


def test_get_dashboard_metrics_forbidden_roles(client, test_session_token):
    """Verify user with unrecognized/forbidden roles receives 403 Forbidden."""
    headers = {
        "Authorization": f"Bearer {test_session_token}",
        "X-User-Roles": "unknown_role_without_access",
    }
    response = client.get("/api/v1/dashboard/metrics", headers=headers)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_get_dashboard_metrics_baseline_parameter(client, test_db, test_session_token):
    """Verify baseline query parameter alters trend calculation period."""
    update_domain_metric_cache(
        db_session=test_db,
        domain_name="finance",
        metric_key="revenue",
        current_value=200000.0,
        unit_type="currency",
    )
    update_historical_snapshot(
        db_session=test_db,
        domain_name="finance",
        metric_key="revenue",
        baseline_period="prior_quarter",
        snapshot_value=100000.0,
    )

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/dashboard/metrics?baseline=prior_quarter", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    finance_domain = next((d for d in data["metrics"] if d["domain_name"] == "finance"), None)
    assert finance_domain is not None
    kpi = finance_domain["kpis"][0]
    assert kpi["trend_direction"] == "up"
    assert kpi["trend_percentage"] == 100.0
