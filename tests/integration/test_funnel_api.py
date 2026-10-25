"""Integration tests for conversion funnel analytics and stage drill-down API endpoints in backend/src/api/funnel.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.funnel import router as funnel_router
from src.models.base import Base, create_db_engine, get_db
from src.models.campaign import Campaign
from src.models.funnel import FunnelStage
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
    app.include_router(funnel_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_get_funnel_stages_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.get("/api/v1/funnel/stages")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_funnel_stages_empty(client, test_session_token):
    """Verify authenticated request returns empty list when no funnel stages exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/funnel/stages", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_get_funnel_stages_success(client, test_db, test_session_token):
    """Verify conversion metrics calculation and bottleneck detection across pipeline stages."""
    stage1 = FunnelStage(
        stage_name="Lead Captured",
        stage_order=1,
        target_conversion_rate=Decimal("100.00"),
    )
    stage2 = FunnelStage(
        stage_name="Qualified Lead",
        stage_order=2,
        target_conversion_rate=Decimal("80.00"),
    )
    stage3 = FunnelStage(
        stage_name="Proposal Sent",
        stage_order=3,
        target_conversion_rate=Decimal("50.00"),
    )
    test_db.add_all([stage1, stage2, stage3])
    test_db.commit()

    source = LeadSource(source_name="Inbound Web", cost_amount=Decimal("100.00"))
    test_db.add(source)
    test_db.commit()

    # Stage 1: 10 leads, Stage 2: 5 leads (50% conv rate < 80% target => bottleneck), Stage 3: 4 leads (80% conv rate > 50% target)
    for _ in range(10):
        test_db.add(
            Lead(
                source_id=source.source_id,
                quality_score=70,
                status_stage=stage1.stage_id,
                deal_value=Decimal("1000.00"),
            )
        )
    for _ in range(5):
        test_db.add(
            Lead(
                source_id=source.source_id,
                quality_score=80,
                status_stage=stage2.stage_id,
                deal_value=Decimal("2000.00"),
            )
        )
    for _ in range(4):
        test_db.add(
            Lead(
                source_id=source.source_id,
                quality_score=90,
                status_stage=stage3.stage_id,
                deal_value=Decimal("5000.00"),
            )
        )
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/funnel/stages", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 3

    st1 = data[0]
    assert st1["stage_name"] == "Lead Captured"
    assert st1["lead_count"] == 10
    assert st1["conversion_rate_percentage"] == 100.0
    assert st1["is_bottleneck"] is False

    st2 = data[1]
    assert st2["stage_name"] == "Qualified Lead"
    assert st2["lead_count"] == 5
    # 5 / 10 = 50.0% < 80.0% target
    assert st2["conversion_rate_percentage"] == 50.0
    assert st2["is_bottleneck"] is True

    st3 = data[2]
    assert st3["stage_name"] == "Proposal Sent"
    assert st3["lead_count"] == 4
    # 4 / 5 = 80.0% > 50.0% target
    assert st3["conversion_rate_percentage"] == 80.0
    assert st3["is_bottleneck"] is False


def test_get_funnel_bottlenecks(client, test_db, test_session_token):
    """Verify endpoint filtering for only bottleneck stages."""
    stage1 = FunnelStage(
        stage_name="Stage 1",
        stage_order=1,
        target_conversion_rate=Decimal("100.00"),
    )
    stage2 = FunnelStage(
        stage_name="Stage 2",
        stage_order=2,
        target_conversion_rate=Decimal("90.00"),
    )
    test_db.add_all([stage1, stage2])
    test_db.commit()

    source = LeadSource(source_name="Direct", cost_amount=Decimal("0.00"))
    test_db.add(source)
    test_db.commit()

    for _ in range(10):
        test_db.add(Lead(source_id=source.source_id, status_stage=stage1.stage_id))
    for _ in range(2):
        test_db.add(Lead(source_id=source.source_id, status_stage=stage2.stage_id))
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/funnel/bottlenecks", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["stage_name"] == "Stage 2"
    assert data[0]["is_bottleneck"] is True


def test_get_funnel_stage_by_id_success(client, test_db, test_session_token):
    """Verify retrieving metrics for a single funnel stage by ID."""
    stage = FunnelStage(
        stage_name="Discovery",
        stage_order=1,
        target_conversion_rate=Decimal("50.00"),
    )
    test_db.add(stage)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get(f"/api/v1/funnel/stages/{stage.stage_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["stage_id"] == stage.stage_id
    assert data["stage_name"] == "Discovery"


def test_get_funnel_stage_by_id_not_found(client, test_session_token):
    """Verify HTTP 404 when requested stage_id does not exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/funnel/stages/non-existent-id", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "FUNNEL_STAGE_NOT_FOUND"


def test_get_stage_lead_drilldown_success(client, test_db, test_session_token):
    """Verify paginated lead details in stage drill-down."""
    stage = FunnelStage(
        stage_name="Negotiation",
        stage_order=1,
        target_conversion_rate=Decimal("70.00"),
    )
    source = LeadSource(source_name="Partner", cost_amount=Decimal("0.00"))
    test_db.add_all([stage, source])
    test_db.commit()

    now = datetime.now(timezone.utc)
    lead1 = Lead(
        source_id=source.source_id,
        quality_score=95,
        status_stage=stage.stage_id,
        region_name="EMEA",
        state_code="CA",
        postal_code="90210",
        deal_value=Decimal("15000.00"),
        created_timestamp=now - timedelta(days=2),
    )
    lead2 = Lead(
        source_id=source.source_id,
        quality_score=85,
        status_stage=stage.stage_id,
        region_name="APAC",
        deal_value=Decimal("8000.00"),
        created_timestamp=now - timedelta(days=1),
    )
    test_db.add_all([lead1, lead2])
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    params = {"page": 1, "limit": 10}
    response = client.get(
        f"/api/v1/funnel/stages/{stage.stage_id}/leads",
        headers=headers,
        params=params,
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["stage_id"] == stage.stage_id
    assert data["stage_name"] == "Negotiation"
    assert data["total_leads"] == 2
    assert data["page"] == 1
    assert data["limit"] == 10
    assert len(data["leads"]) == 2

    first_lead = data["leads"][0]
    # Ordered by created_timestamp desc => lead2 first
    assert first_lead["lead_id"] == lead2.lead_id
    assert first_lead["quality_score"] == 85
    assert first_lead["region_name"] == "APAC"
    assert first_lead["deal_value"] == 8000.0
    assert first_lead["time_in_stage_days"] >= 0.0


def test_get_stage_lead_drilldown_not_found(client, test_session_token):
    """Verify HTTP 404 when drill-down requested for non-existent stage."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/funnel/stages/invalid-stage/leads", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "FUNNEL_STAGE_NOT_FOUND"


def test_get_funnel_stages_invalid_date_range(client, test_session_token):
    """Verify HTTP 400 Bad Request when start_timestamp is after end_timestamp."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(days=5)

    params = {
        "start_timestamp": now.isoformat(),
        "end_timestamp": earlier.isoformat(),
    }
    response = client.get("/api/v1/funnel/stages", headers=headers, params=params)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["detail"]["code"] == "INVALID_DATE_RANGE"
