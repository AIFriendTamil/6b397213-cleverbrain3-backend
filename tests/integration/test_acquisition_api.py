"""Integration tests for Lead Acquisition source and ROI API endpoints in backend/src/api/acquisition.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.acquisition import router as acquisition_router
from src.models.base import Base, create_db_engine, get_db
from src.models.campaign import Campaign
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
    tenant = TenantModel(name="Analytics Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="analyst@example.com",
        password_hash="hashed_secret",
        full_name="Analytics User",
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
    app.include_router(acquisition_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_get_acquisition_sources_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.get("/api/v1/acquisition/sources")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_acquisition_sources_empty(client, test_session_token):
    """Verify authenticated request returns empty list when no lead sources exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/acquisition/sources", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_get_acquisition_sources_success(client, test_db, test_session_token):
    """Verify metrics calculation for acquisition sources with leads."""
    source1 = LeadSource(
        source_name="Paid Search Ads",
        cost_amount=Decimal("1000.00"),
    )
    source2 = LeadSource(
        source_name="Organic Referrals",
        cost_amount=Decimal("500.00"),
    )
    test_db.add_all([source1, source2])
    test_db.commit()

    lead1 = Lead(
        source_id=source1.source_id,
        quality_score=80,
        deal_value=Decimal("3000.00"),
        region_name="North America",
    )
    lead2 = Lead(
        source_id=source1.source_id,
        quality_score=90,
        deal_value=Decimal("2000.00"),
        region_name="North America",
    )
    test_db.add_all([lead1, lead2])
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/acquisition/sources", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2

    # Results ordered by source_name: "Organic Referrals", "Paid Search Ads"
    organic = data[0]
    assert organic["source_name"] == "Organic Referrals"
    assert organic["total_leads"] == 0
    assert organic["average_quality_score"] == 0.0
    assert organic["total_spend"] == 500.0
    assert organic["attributed_revenue"] == 0.0
    assert organic["calculated_roi_percentage"] == 0.0

    paid = data[1]
    assert paid["source_name"] == "Paid Search Ads"
    assert paid["total_leads"] == 2
    assert paid["average_quality_score"] == 85.0
    assert paid["total_spend"] == 1000.0
    assert paid["attributed_revenue"] == 5000.0
    # ((5000 - 1000) / 1000) * 100 = 400.0%
    assert paid["calculated_roi_percentage"] == 400.0


def test_get_acquisition_sources_invalid_date_range(client, test_session_token):
    """Verify HTTP 400 Bad Request when start_timestamp is after end_timestamp."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(days=5)

    params = {
        "start_timestamp": now.isoformat(),
        "end_timestamp": earlier.isoformat(),
    }
    response = client.get("/api/v1/acquisition/sources", headers=headers, params=params)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["detail"]["code"] == "INVALID_DATE_RANGE"


def test_get_acquisition_sources_filtered(client, test_db, test_session_token):
    """Verify filtering by source_id, region_name, and campaign_id."""
    campaign = Campaign(
        campaign_name="Q4 Growth",
        budget_amount=Decimal("5000.00"),
        spent_amount=Decimal("2000.00"),
    )
    source = LeadSource(
        source_name="Email Marketing",
        cost_amount=Decimal("800.00"),
    )
    test_db.add_all([campaign, source])
    test_db.commit()

    lead1 = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=75,
        deal_value=Decimal("1500.00"),
        region_name="EMEA",
    )
    lead2 = Lead(
        source_id=source.source_id,
        quality_score=65,
        deal_value=Decimal("800.00"),
        region_name="APAC",
    )
    test_db.add_all([lead1, lead2])
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}

    # Filter by region_name="EMEA"
    resp = client.get(
        "/api/v1/acquisition/sources",
        headers=headers,
        params={"region_name": "EMEA"},
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    assert data[0]["total_leads"] == 1
    assert data[0]["attributed_revenue"] == 1500.0

    # Filter by campaign_id
    resp = client.get(
        "/api/v1/acquisition/sources",
        headers=headers,
        params={"campaign_id": campaign.campaign_id},
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    assert data[0]["total_leads"] == 1


def test_get_acquisition_summary(client, test_db, test_session_token):
    """Verify GET /api/v1/acquisition/summary returns aggregate statistics across sources."""
    source = LeadSource(
        source_name="Direct Outbound",
        cost_amount=Decimal("1000.00"),
    )
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=90,
        deal_value=Decimal("4000.00"),
    )
    test_db.add(lead)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/acquisition/summary", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["total_sources"] == 1
    assert data["total_leads"] == 1
    assert data["overall_average_quality_score"] == 90.0
    assert data["total_spend"] == 1000.0
    assert data["total_attributed_revenue"] == 4000.0
    assert data["overall_roi_percentage"] == 300.0


def test_get_acquisition_source_by_id_success(client, test_db, test_session_token):
    """Verify GET /api/v1/acquisition/sources/{source_id} returns metrics for valid ID."""
    source = LeadSource(
        source_name="Partner Network",
        cost_amount=Decimal("1200.00"),
    )
    test_db.add(source)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get(f"/api/v1/acquisition/sources/{source.source_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["source_id"] == source.source_id
    assert data["source_name"] == "Partner Network"


def test_get_acquisition_source_by_id_not_found(client, test_session_token):
    """Verify GET /api/v1/acquisition/sources/{source_id} returns 404 for invalid ID."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/acquisition/sources/non-existent-id", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "SOURCE_NOT_FOUND"
