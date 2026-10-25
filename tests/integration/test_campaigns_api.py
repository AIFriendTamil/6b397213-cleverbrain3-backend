"""Integration tests for side-by-side Campaign comparison matrix API endpoints in backend/src/api/campaigns.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.campaigns import router as campaigns_router
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
    tenant = TenantModel(name="Campaign Analytics Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="campaigns@example.com",
        password_hash="hashed_secret",
        full_name="Campaign Analyst",
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
    app.include_router(campaigns_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_get_campaign_comparison_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.get("/api/v1/campaigns/compare")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_campaign_comparison_empty_db(client, test_session_token):
    """Verify authenticated request returns empty comparison matrix when no campaigns exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/campaigns/compare", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["campaigns"] == []
    assert data["summary"]["total_campaigns_compared"] == 0
    assert data["summary"]["best_roi_campaign_id"] is None


def test_get_campaign_comparison_invalid_date_range(client, test_session_token):
    """Verify endpoint returns 400 Bad Request when start_timestamp > end_timestamp."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    now = datetime.now(timezone.utc)
    start_ts = now.isoformat()
    end_ts = (now - timedelta(days=5)).isoformat()

    response = client.get(
        f"/api/v1/campaigns/compare?start_timestamp={start_ts}&end_timestamp={end_ts}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["detail"]["code"] == "INVALID_DATE_RANGE"


def test_get_campaign_comparison_success(client, test_db, test_session_token):
    """Verify side-by-side performance comparison matrix across multiple marketing campaigns."""
    headers = {"Authorization": f"Bearer {test_session_token}"}

    # Create test campaigns
    c1 = Campaign(
        campaign_name="Alpha Campaign",
        budget_amount=Decimal("5000.00"),
        spent_amount=Decimal("2000.00"),
    )
    c2 = Campaign(
        campaign_name="Beta Campaign",
        budget_amount=Decimal("10000.00"),
        spent_amount=Decimal("4000.00"),
    )
    test_db.add_all([c1, c2])
    test_db.commit()

    source = LeadSource(source_name="Direct Search")
    test_db.add(source)
    test_db.commit()

    # Add leads for Alpha Campaign
    l1 = Lead(
        source_id=source.source_id,
        campaign_id=c1.campaign_id,
        quality_score=90,
        status_stage="CLOSED_WON",
        deal_value=Decimal("8000.00"),
    )
    # Add leads for Beta Campaign
    l2 = Lead(
        source_id=source.source_id,
        campaign_id=c2.campaign_id,
        quality_score=50,
        status_stage="QUALIFIED",
        deal_value=Decimal("0.00"),
    )
    test_db.add_all([l1, l2])
    test_db.commit()

    response = client.get("/api/v1/campaigns/compare", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert len(data["campaigns"]) == 2
    summary = data["summary"]
    assert summary["total_campaigns_compared"] == 2
    assert summary["total_spend_across_campaigns"] == 6000.0
    assert summary["total_revenue_across_campaigns"] == 8000.0
    assert summary["total_leads_across_campaigns"] == 2
    assert summary["best_roi_campaign_id"] == c1.campaign_id


def test_get_campaign_comparison_filtered_by_campaign_ids(client, test_db, test_session_token):
    """Verify comparison matrix filtering by specific campaign_ids query parameters."""
    headers = {"Authorization": f"Bearer {test_session_token}"}

    c1 = Campaign(campaign_name="Campaign 1", budget_amount=Decimal("1000"), spent_amount=Decimal("500"))
    c2 = Campaign(campaign_name="Campaign 2", budget_amount=Decimal("1000"), spent_amount=Decimal("500"))
    c3 = Campaign(campaign_name="Campaign 3", budget_amount=Decimal("1000"), spent_amount=Decimal("500"))
    test_db.add_all([c1, c2, c3])
    test_db.commit()

    response = client.get(
        f"/api/v1/campaigns/compare?campaign_ids={c1.campaign_id},{c2.campaign_id}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert len(data["campaigns"]) == 2
    returned_ids = {c["campaign_id"] for c in data["campaigns"]}
    assert returned_ids == {c1.campaign_id, c2.campaign_id}


def test_list_campaigns(client, test_db, test_session_token):
    """Verify listing all campaigns ordered by name."""
    headers = {"Authorization": f"Bearer {test_session_token}"}

    c1 = Campaign(campaign_name="Zeta Campaign", budget_amount=Decimal("1000"), spent_amount=Decimal("500"))
    c2 = Campaign(campaign_name="Gamma Campaign", budget_amount=Decimal("1000"), spent_amount=Decimal("500"))
    test_db.add_all([c1, c2])
    test_db.commit()

    response = client.get("/api/v1/campaigns", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert len(data) == 2
    assert data[0]["campaign_name"] == "Gamma Campaign"
    assert data[1]["campaign_name"] == "Zeta Campaign"


def test_get_campaign_detail_success_and_not_found(client, test_db, test_session_token):
    """Verify retrieving single campaign detail and 404 behavior for missing campaign."""
    headers = {"Authorization": f"Bearer {test_session_token}"}

    c = Campaign(campaign_name="Target Campaign", budget_amount=Decimal("3000"), spent_amount=Decimal("1500"))
    test_db.add(c)
    test_db.commit()

    # Success case
    response = client.get(f"/api/v1/campaigns/{c.campaign_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["campaign_id"] == c.campaign_id
    assert data["campaign_name"] == "Target Campaign"

    # 404 case
    response_404 = client.get("/api/v1/campaigns/non-existent-uuid", headers=headers)
    assert response_404.status_code == status.HTTP_404_NOT_FOUND
    assert response_404.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"
