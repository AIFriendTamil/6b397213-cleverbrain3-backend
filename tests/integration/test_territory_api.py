"""Integration tests for Geographic Territory density API endpoints in backend/src/api/territory.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.territory import router as territory_router
from src.lib.config import settings
from src.models.base import Base, create_db_engine, get_db
from src.models.lead import Lead, LeadSource
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
    """Fixture creating tenant, user, and active session records, returning session token string."""
    tenant = TenantModel(name="Territory Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="territory@example.com",
        password_hash="hashed_secret",
        full_name="Territory Analyst",
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
    """Fixture providing a FastAPI TestClient with database session override."""
    app = FastAPI()
    app.include_router(territory_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_get_territory_heatmap_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.get("/api/v1/territory/heatmap")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_territory_heatmap_empty(client, test_session_token):
    """Verify authenticated request returns empty list when no leads exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/territory/heatmap", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_get_territory_heatmap_success(client, test_db, test_session_token):
    """Verify territory density metrics aggregation and filtering across regions."""
    source = LeadSource(source_name="Inbound Web")
    test_db.add(source)
    test_db.commit()

    # Create leads across regions
    l1 = Lead(
        source_id=source.source_id,
        region_name="North America",
        deal_value=Decimal("10000.00"),
        quality_score=90,
    )
    l2 = Lead(
        source_id=source.source_id,
        region_name="North America",
        deal_value=Decimal("5000.00"),
        quality_score=80,
    )
    l3 = Lead(
        source_id=source.source_id,
        region_name="Europe",
        deal_value=Decimal("8000.00"),
        quality_score=70,
    )
    l4 = Lead(
        source_id=source.source_id,
        region_name=None,  # Unmapped
        deal_value=Decimal("2000.00"),
        quality_score=60,
    )
    test_db.add_all([l1, l2, l3, l4])
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/territory/heatmap", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data) == 3

    # North America should be first (2 opportunities = 50% density)
    na = data[0]
    assert na["region_name"] == "North America"
    assert na["opportunity_count"] == 2
    assert na["total_deal_value"] == 15000.0
    assert na["average_quality_score"] == 85.0
    assert na["density_score"] == 50.0
    assert na["latitude"] == 37.0902
    assert na["longitude"] == -95.7129

    # Filter by min_quality_score = 80
    res_filtered = client.get(
        "/api/v1/territory/heatmap?min_quality_score=80", headers=headers
    )
    assert res_filtered.status_code == status.HTTP_200_OK
    filtered_data = res_filtered.json()
    assert len(filtered_data) == 1
    assert filtered_data[0]["region_name"] == "North America"
    assert filtered_data[0]["opportunity_count"] == 2


def test_get_territory_heatmap_invalid_date_range(client, test_session_token):
    """Verify 400 Bad Request returned when start_timestamp > end_timestamp."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    now = datetime.now(timezone.utc)
    start = (now + timedelta(days=5)).isoformat()
    end = now.isoformat()

    response = client.get(
        f"/api/v1/territory/heatmap?start_timestamp={start}&end_timestamp={end}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["detail"]["code"] == "INVALID_DATE_RANGE"


def test_get_territory_heatmap_invalid_numeric_filters(client, test_session_token):
    """Verify 400 Bad Request returned when numeric filter parameters violate boundaries."""
    headers = {"Authorization": f"Bearer {test_session_token}"}

    # Negative deal size
    res1 = client.get("/api/v1/territory/heatmap?min_deal_size=-10", headers=headers)
    assert res1.status_code == status.HTTP_400_BAD_REQUEST

    # Quality score out of bounds
    res2 = client.get(
        "/api/v1/territory/heatmap?min_quality_score=150", headers=headers
    )
    assert res2.status_code == status.HTTP_400_BAD_REQUEST


def test_get_unmapped_territory_unauthenticated(client):
    """Verify 401 Unauthorized when requesting unmapped territory metrics without authentication."""
    response = client.get("/api/v1/territory/unmapped")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_unmapped_territory_empty(client, test_session_token):
    """Verify default zero metrics returned when no unmapped leads exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/territory/unmapped", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["region_name"] == settings.DEFAULT_UNMAPPED_REGION_NAME
    assert data["opportunity_count"] == 0
    assert data["total_deal_value"] == 0.0
    assert data["average_quality_score"] == 0.0
    assert data["density_score"] == 0.0
    assert data["latitude"] == 0.0
    assert data["longitude"] == 0.0


def test_get_unmapped_territory_success(client, test_db, test_session_token):
    """Verify unmapped territory metrics calculation when unmapped leads exist."""
    source = LeadSource(source_name="Direct Input")
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        region_name="",  # Unmapped
        deal_value=Decimal("3500.00"),
        quality_score=75,
    )
    test_db.add(lead)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/territory/unmapped", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["region_name"] == settings.DEFAULT_UNMAPPED_REGION_NAME
    assert data["opportunity_count"] == 1
    assert data["total_deal_value"] == 3500.0
    assert data["average_quality_score"] == 75.0
    assert data["density_score"] == 100.0
