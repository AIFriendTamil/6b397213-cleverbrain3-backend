"""Integration tests for POST /api/v1/dashboard/refresh API endpoint."""

from datetime import datetime, timezone
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.dashboard import router as dashboard_router
from src.models.base import Base, create_db_engine, get_db
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel


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
    tenant = TenantModel(name="Test Executive Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="executive_refresh@example.com",
        password_hash="hashed_secret",
        full_name="Refresh Executive User",
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


def test_post_dashboard_refresh_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.post("/api/v1/dashboard/refresh")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_post_dashboard_refresh_authenticated_success(client, test_session_token):
    """Verify authenticated user receives 202 Accepted with refresh status and job_id."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.post("/api/v1/dashboard/refresh", headers=headers)

    assert response.status_code == status.HTTP_202_ACCEPTED
    data = response.json()
    assert data["status"] == "refresh_initiated"
    assert "job_id" in data
    assert isinstance(data["job_id"], str)
    assert len(data["job_id"]) > 0


def test_post_dashboard_refresh_with_requested_domains(client, test_session_token):
    """Verify refresh endpoint accepts optional domains list in request body."""
    headers = {
        "Authorization": f"Bearer {test_session_token}",
        "X-User-Roles": "sales_executive",
    }
    payload = {"domains": ["sales", "leads"]}
    response = client.post("/api/v1/dashboard/refresh", headers=headers, json=payload)

    assert response.status_code == status.HTTP_202_ACCEPTED
    data = response.json()
    assert data["status"] == "refresh_initiated"
    assert "job_id" in data


def test_post_dashboard_refresh_forbidden_roles(client, test_session_token):
    """Verify user with unrecognized/forbidden roles receives 403 Forbidden."""
    headers = {
        "Authorization": f"Bearer {test_session_token}",
        "X-User-Roles": "unknown_role_without_access",
    }
    response = client.post("/api/v1/dashboard/refresh", headers=headers)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"


def test_post_dashboard_refresh_unauthorized_requested_domain(client, test_session_token):
    """Verify user requesting domain outside authorized roles receives 403 Forbidden."""
    headers = {
        "Authorization": f"Bearer {test_session_token}",
        "X-User-Roles": "support_agent",  # Authorized for support only
    }
    payload = {"domains": ["finance"]}  # Requesting finance domain
    response = client.post("/api/v1/dashboard/refresh", headers=headers, json=payload)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["detail"]["code"] == "FORBIDDEN"
