"""Integration tests for Scheduled Meetings API endpoints in backend/src/api/meetings.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.meetings import router as meetings_router
from src.models.base import Base, create_db_engine, get_db
from src.models.lead import Lead, LeadSource
from src.models.meeting import ScheduledMeeting
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
    tenant = TenantModel(name="Meetings Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="sales.rep@example.com",
        password_hash="hashed_secret",
        full_name="Sales Representative",
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
    app.include_router(meetings_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_get_scheduled_meetings_unauthenticated(client):
    """Verify endpoint returns 401 Unauthorized when session token is missing."""
    response = client.get("/api/v1/meetings")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["detail"]["code"] == "UNAUTHORIZED"


def test_get_scheduled_meetings_empty(client, test_session_token):
    """Verify authenticated request returns empty list when no scheduled meetings exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/meetings", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_get_scheduled_meetings_success(client, test_db, test_session_token):
    """Verify list of scheduled meetings returned when records exist in database."""
    source = LeadSource(source_name="Inbound Web")
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=85,
        status_stage="QUALIFIED",
        deal_value=Decimal("12000.00"),
    )
    test_db.add(lead)
    test_db.commit()

    start_time = datetime.now(timezone.utc) + timedelta(days=1)
    end_time = start_time + timedelta(minutes=45)

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Strategy Alignment Sync",
        start_timestamp=start_time,
        end_timestamp=end_time,
        brief_summary_text="Initial product strategy discussion",
    )
    test_db.add(meeting)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/meetings", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data) == 1
    m = data[0]
    assert m["title"] == "Strategy Alignment Sync"
    assert m["lead_id"] == lead.lead_id
    assert m["meeting_id"] == meeting.meeting_id


def test_get_scheduled_meetings_invalid_date_range(client, test_session_token):
    """Verify 400 Bad Request returned when start_timestamp > end_timestamp."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    now = datetime.now(timezone.utc)
    start = (now + timedelta(days=5)).isoformat()
    end = now.isoformat()

    response = client.get(
        f"/api/v1/meetings?start_timestamp={start}&end_timestamp={end}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["detail"]["code"] == "INVALID_DATE_RANGE"


def test_create_scheduled_meeting_unauthenticated(client):
    """Verify 401 Unauthorized when creating a meeting without authentication."""
    payload = {
        "title": "Discovery Call",
        "start_timestamp": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "end_timestamp": (datetime.now(timezone.utc) + timedelta(days=1, hours=1)).isoformat(),
    }
    response = client.post("/api/v1/meetings", json=payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_scheduled_meeting_success(client, test_db, test_session_token):
    """Verify successful meeting creation with valid payload and lead."""
    source = LeadSource(source_name="Partner Network")
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=90,
        status_stage="DEMO",
    )
    test_db.add(lead)
    test_db.commit()

    start_dt = datetime.now(timezone.utc) + timedelta(days=2)
    end_dt = start_dt + timedelta(hours=1)

    payload = {
        "title": "Executive Product Demo",
        "start_timestamp": start_dt.isoformat(),
        "end_timestamp": end_dt.isoformat(),
        "lead_id": lead.lead_id,
        "description": "Demonstrating core enterprise features.",
        "location": "https://meet.example.com/demo-room",
    }

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.post("/api/v1/meetings", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED

    data = response.json()
    assert data["title"] == "Executive Product Demo"
    assert data["lead_id"] == lead.lead_id
    assert data["location"] == "https://meet.example.com/demo-room"
    assert data["description"] == "Demonstrating core enterprise features."


def test_create_scheduled_meeting_nonexistent_lead(client, test_session_token):
    """Verify 404 Not Found returned when specifying non-existent lead_id."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    start_dt = datetime.now(timezone.utc) + timedelta(days=2)
    end_dt = start_dt + timedelta(hours=1)

    payload = {
        "title": "Discovery Call",
        "start_timestamp": start_dt.isoformat(),
        "end_timestamp": end_dt.isoformat(),
        "lead_id": "nonexistent-lead-id",
    }

    response = client.post("/api/v1/meetings", json=payload, headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "LEAD_NOT_FOUND"


def test_get_scheduled_meeting_by_id_success(client, test_db, test_session_token):
    """Verify fetching single scheduled meeting details by ID."""
    source = LeadSource(source_name="Inbound Call")
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=78,
        status_stage="QUALIFIED",
    )
    test_db.add(lead)
    test_db.commit()

    start_dt = datetime.now(timezone.utc) + timedelta(days=3)
    end_dt = start_dt + timedelta(minutes=30)

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Technical Scope Review",
        start_timestamp=start_dt,
        end_timestamp=end_dt,
    )
    test_db.add(meeting)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get(f"/api/v1/meetings/{meeting.meeting_id}", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["event_id"] == meeting.meeting_id
    assert data["meeting_id"] == meeting.meeting_id
    assert data["title"] == "Technical Scope Review"


def test_get_scheduled_meeting_by_id_not_found(client, test_session_token):
    """Verify 404 Not Found returned when meeting ID does not exist."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/meetings/nonexistent-meeting-id", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "MEETING_NOT_FOUND"


def test_get_meeting_preparation_brief_success(client, test_db, test_session_token):
    """Verify synthesis and retrieval of meeting preparation brief."""
    source = LeadSource(source_name="Outbound Email")
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=88,
        status_stage="PROPOSAL",
        deal_value=Decimal("20000.00"),
        region_name="North America",
    )
    test_db.add(lead)
    test_db.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Proposal Review Meeting",
        start_timestamp=datetime.now(timezone.utc) + timedelta(days=1),
        end_timestamp=datetime.now(timezone.utc) + timedelta(days=1, hours=1),
    )
    test_db.add(meeting)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get(f"/api/v1/meetings/{meeting.meeting_id}/brief", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["meeting_id"] == meeting.meeting_id
    assert data["lead_id"] == lead.lead_id
    assert data["quality_score"] == 88
    assert "Lead Profile & Context:" in data["interaction_history_summary"]
    assert "Outbound Email" in data["interaction_history_summary"]
    assert "Commercial Proposal" in data["key_discussion_notes"]
    assert "Preparation brief for 'Proposal Review Meeting'" in data["brief_summary_text"]


def test_get_meeting_preparation_brief_not_found(client, test_session_token):
    """Verify 404 Not Found returned when brief requested for unknown meeting ID."""
    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/meetings/unknown-meeting-id/brief", headers=headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["detail"]["code"] == "MEETING_NOT_FOUND"
