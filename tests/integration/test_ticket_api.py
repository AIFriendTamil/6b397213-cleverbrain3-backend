"""Integration tests for Support Ticket API endpoints in backend/src/api/tickets.py."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.csat import CSATResponse
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.ticket import Ticket as TicketModel, TicketSeverity, TicketStatus
from src.models.user import User as UserModel


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def test_session_data(test_db):
    """Fixture creating primary tenant, user, and active session records."""
    tenant = TenantModel(name="Primary Support Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="support_user@example.com",
        password_hash="secret_hash",
        full_name="Support Test User",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def other_tenant_session_data(test_db):
    """Fixture creating secondary tenant, user, and active session records for isolation testing."""
    tenant = TenantModel(name="Other Support Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="other_user@example.com",
        password_hash="secret_hash",
        full_name="Other Test User",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def client(test_db):
    """Fixture overriding get_db dependency and returning a FastAPI TestClient instance."""
    app_instance = create_app()

    def _get_db_override():
        yield test_db

    app_instance.dependency_overrides[get_db] = _get_db_override
    with TestClient(app_instance) as test_client:
        yield test_client
    app_instance.dependency_overrides.clear()


# --- Unauthenticated Access Tests ---


def test_unauthenticated_ticket_access_denied(client):
    """Verify unauthenticated API calls return 401 Unauthorized."""
    res = client.get("/api/v1/tickets")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    res = client.post("/api/v1/tickets", json={"title": "Test Ticket", "description": "Desc"})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


# --- Ticket CRUD, Attachments, Status Updates, CSAT & Delete Tests ---


def test_create_and_get_ticket_json(client, test_session_data):
    """Verify ticket creation via JSON payload and subsequent retrieval."""
    headers = {"X-Session-ID": test_session_data.id}
    payload = {
        "title": "System Outage Error",
        "description": "Database connection pooling error in production.",
        "severity": "P1",
    }

    res = client.post("/api/v1/tickets", json=payload, headers=headers)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["title"] == "System Outage Error"
    assert data["severity"] == "P1"
    assert data["status"] == "NEW"
    assert data["organization_id"] == test_session_data.tenant_id
    ticket_id = data["id"]

    # Retrieve detail
    get_res = client.get(f"/api/v1/tickets/{ticket_id}", headers=headers)
    assert get_res.status_code == status.HTTP_200_OK
    detail_data = get_res.json()
    assert detail_data["id"] == ticket_id
    assert len(detail_data["events"]) >= 1


def test_create_ticket_multipart_with_attachment(client, test_session_data, tmp_path):
    """Verify ticket creation via multipart form data with file attachment."""
    headers = {"X-Session-ID": test_session_data.id}
    form_data = {
        "title": "Hardware Failure Report",
        "description": "Server fan stopped functioning.",
        "severity": "P2",
    }
    files = {
        "file": ("system_log.txt", b"Log file content lines...", "text/plain")
    }

    res = client.post("/api/v1/tickets", data=form_data, files=files, headers=headers)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["title"] == "Hardware Failure Report"
    assert data["severity"] == "P2"


def test_create_ticket_validation_failure(client, test_session_data):
    """Verify validation failure when required fields are missing."""
    headers = {"X-Session-ID": test_session_data.id}
    payload = {"title": "", "description": ""}

    res = client.post("/api/v1/tickets", json=payload, headers=headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    err = res.json()
    assert "detail" in err


def test_list_tickets_filtering_and_pagination(client, test_session_data):
    """Verify query filtering and pagination on list tickets endpoint."""
    headers = {"X-Session-ID": test_session_data.id}

    # Create 3 tickets
    client.post("/api/v1/tickets", json={"title": "Ticket A", "description": "Desc A", "severity": "P1"}, headers=headers)
    client.post("/api/v1/tickets", json={"title": "Ticket B", "description": "Desc B", "severity": "P2"}, headers=headers)
    client.post("/api/v1/tickets", json={"title": "Ticket C", "description": "Desc C", "severity": "P3"}, headers=headers)

    res = client.get("/api/v1/tickets?severity=P1", headers=headers)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Ticket A"

    res_all = client.get("/api/v1/tickets?page=1&limit=2", headers=headers)
    assert res_all.status_code == status.HTTP_200_OK
    data_all = res_all.json()
    assert data_all["total"] == 3
    assert len(data_all["items"]) == 2
    assert data_all["pages"] == 2


def test_add_reply_to_ticket(client, test_session_data):
    """Verify adding a reply/comment to a support ticket."""
    headers = {"X-Session-ID": test_session_data.id}
    create_res = client.post("/api/v1/tickets", json={"title": "Reply Test", "description": "Initial desc"}, headers=headers)
    ticket_id = create_res.json()["id"]

    reply_payload = {"content": "Investigating this issue right now."}
    reply_res = client.post(f"/api/v1/tickets/{ticket_id}/replies", json=reply_payload, headers=headers)
    assert reply_res.status_code == status.HTTP_201_CREATED
    reply_data = reply_res.json()
    assert reply_data["content"] == "Investigating this issue right now."
    assert reply_data["event_type"] == "COMMENT"


def test_update_status_and_csat_workflow(client, test_session_data):
    """Verify status update to RESOLVED and post-resolution CSAT submission."""
    headers = {"X-Session-ID": test_session_data.id}
    create_res = client.post("/api/v1/tickets", json={"title": "CSAT Workflow Test", "description": "Desc"}, headers=headers)
    ticket_id = create_res.json()["id"]

    # CSAT submission should fail on NEW ticket
    csat_res_fail = client.post(f"/api/v1/tickets/{ticket_id}/csat", json={"rating": 5}, headers=headers)
    assert csat_res_fail.status_code == status.HTTP_400_BAD_REQUEST

    # Update status to RESOLVED
    update_res = client.patch(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "RESOLVED", "severity": "P2", "comment": "Issue fixed in release 1.2."},
        headers=headers,
    )
    assert update_res.status_code == status.HTTP_200_OK
    assert update_res.json()["status"] == "RESOLVED"
    assert update_res.json()["severity"] == "P2"

    # Submit CSAT rating
    csat_res = client.post(
        f"/api/v1/tickets/{ticket_id}/csat",
        json={"rating": 5, "feedback_text": "Great support team!"},
        headers=headers,
    )
    assert csat_res.status_code == status.HTTP_201_CREATED
    csat_data = csat_res.json()
    assert csat_data["rating"] == 5
    assert csat_data["feedback_text"] == "Great support team!"

    # Duplicate CSAT submission should fail
    csat_dup_res = client.post(
        f"/api/v1/tickets/{ticket_id}/csat",
        json={"rating": 4},
        headers=headers,
    )
    assert csat_dup_res.status_code == status.HTTP_400_BAD_REQUEST


def test_delete_ticket_endpoint(client, test_session_data):
    """Verify DELETE endpoint removes ticket and returns 204 No Content with empty body."""
    headers = {"X-Session-ID": test_session_data.id}
    create_res = client.post("/api/v1/tickets", json={"title": "Ticket to Delete", "description": "Desc"}, headers=headers)
    ticket_id = create_res.json()["id"]

    del_res = client.delete(f"/api/v1/tickets/{ticket_id}", headers=headers)
    assert del_res.status_code == status.HTTP_204_NO_CONTENT
    assert del_res.content == b""  # Empty body for 204 response

    # Verify ticket no longer exists
    get_res = client.get(f"/api/v1/tickets/{ticket_id}", headers=headers)
    assert get_res.status_code == status.HTTP_404_NOT_FOUND


def test_tenant_isolation_boundary_enforcement(client, test_session_data, other_tenant_session_data):
    """Verify strict tenant isolation boundary enforcement."""
    primary_headers = {"X-Session-ID": test_session_data.id}
    other_headers = {"X-Session-ID": other_tenant_session_data.id}

    # Primary user creates ticket
    create_res = client.post("/api/v1/tickets", json={"title": "Primary Ticket", "description": "Desc"}, headers=primary_headers)
    ticket_id = create_res.json()["id"]

    # Other tenant user tries to get primary user's ticket
    other_get_res = client.get(f"/api/v1/tickets/{ticket_id}", headers=other_headers)
    assert other_get_res.status_code == status.HTTP_403_FORBIDDEN

    # Other tenant user tries to delete primary user's ticket
    other_del_res = client.delete(f"/api/v1/tickets/{ticket_id}", headers=other_headers)
    assert other_del_res.status_code == status.HTTP_403_FORBIDDEN
