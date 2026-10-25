"""Integration tests for notification preference matrix and notification hub CRUD endpoints in backend/src/api/notification_routes.py."""

import uuid
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.notification import NotificationCategory
from src.services.notifications.hub import create_notification, get_user_notifications


@pytest.fixture
def test_db():
    """Fixture providing an ephemeral in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def client(test_db: DBSession):
    """Fixture providing a TestClient with database session dependency override."""
    app_instance = create_app()

    def _override_get_db():
        try:
            yield test_db
        finally:
            pass

    app_instance.dependency_overrides[get_db] = _override_get_db
    with TestClient(app_instance) as test_client:
        yield test_client


def test_get_preferences_endpoint(client: TestClient):
    """Verify GET /api/v1/notifications/preferences initializes and returns default preferences."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    response = client.get("/api/v1/notifications/preferences", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 3

    categories = {p["category"] for p in data}
    assert categories == {"Financial", "Milestone", "SLA"}

    for pref in data:
        assert pref["user_id"] == user_id
        assert pref["channel_email"] is True
        assert pref["channel_sms"] is False
        assert pref["channel_in_app"] is True
        assert pref["phone_number"] is None


def test_update_preferences_endpoint_success(client: TestClient):
    """Verify PUT /api/v1/notifications/preferences updates channel toggles and phone number."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    payload = [
        {
            "category": "Financial",
            "channel_email": False,
            "channel_sms": True,
            "channel_in_app": True,
            "phone_number": "+14155552671",
        },
        {
            "category": "SLA",
            "channel_email": True,
            "channel_sms": False,
            "channel_in_app": False,
        },
    ]

    response = client.put("/api/v1/notifications/preferences", json=payload, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 3

    fin_pref = next(p for p in data if p["category"] == "Financial")
    assert fin_pref["channel_email"] is False
    assert fin_pref["channel_sms"] is True
    assert fin_pref["phone_number"] == "+14155552671"

    sla_pref = next(p for p in data if p["category"] == "SLA")
    assert sla_pref["channel_email"] is True
    assert sla_pref["channel_sms"] is False
    assert sla_pref["channel_in_app"] is False


def test_update_preferences_invalid_phone_format(client: TestClient):
    """Verify PUT /api/v1/notifications/preferences returns HTTP 400 when enabling SMS with invalid phone format."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    payload = [
        {
            "category": "Financial",
            "channel_sms": True,
            "phone_number": "invalid-phone",
        }
    ]

    response = client.put("/api/v1/notifications/preferences", json=payload, headers=headers)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    err_data = response.json()
    assert err_data["detail"]["code"] == "VALIDATION_ERROR"


def test_get_notifications_hub_and_filtering(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/notifications/hub retrieves notifications with category and is_read filtering."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    # Seed test notifications
    n1 = create_notification(test_db, user_id, "Financial", "Invoice Due", "Invoice #1001 is due")
    n2 = create_notification(test_db, user_id, "SLA", "SLA Breach Warning", "Ticket #500 breached SLA")
    n3 = create_notification(test_db, user_id, "Financial", "Payment Received", "Payment for #1001 received")

    # Get all notifications for user
    res_all = client.get("/api/v1/notifications/hub", headers=headers)
    assert res_all.status_code == status.HTTP_200_OK
    all_items = res_all.json()
    assert len(all_items) == 3

    # Filter by category = Financial
    res_fin = client.get("/api/v1/notifications/hub?category=Financial", headers=headers)
    assert res_fin.status_code == status.HTTP_200_OK
    fin_items = res_fin.json()
    assert len(fin_items) == 2

    # Filter by is_read = false
    res_unread = client.get("/api/v1/notifications/hub?is_read=false", headers=headers)
    assert res_unread.status_code == status.HTTP_200_OK
    unread_items = res_unread.json()
    assert len(unread_items) == 3


def test_patch_notification_read_status(client: TestClient, test_db: DBSession):
    """Verify PATCH /api/v1/notifications/hub/{id}/read updates notification read status."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    n1 = create_notification(test_db, user_id, "SLA", "Breach Alert", "High priority ticket breach")

    # Mark as read
    res_patch = client.patch(
        f"/api/v1/notifications/hub/{n1.id}/read",
        json={"is_read": True},
        headers=headers,
    )
    assert res_patch.status_code == status.HTTP_200_OK
    data = res_patch.json()
    assert data["id"] == n1.id
    assert data["is_read"] is True

    # Check 404 for invalid ID
    fake_id = str(uuid.uuid4())
    res_404 = client.patch(f"/api/v1/notifications/hub/{fake_id}/read", json={"is_read": True}, headers=headers)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND


def test_mark_all_read_endpoint(client: TestClient, test_db: DBSession):
    """Verify POST /api/v1/notifications/hub/mark-all-read bulk updates unread notifications."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    create_notification(test_db, user_id, "Financial", "Alert 1", "Message 1")
    create_notification(test_db, user_id, "Milestone", "Alert 2", "Message 2")
    create_notification(test_db, user_id, "SLA", "Alert 3", "Message 3")

    res_mark = client.post("/api/v1/notifications/hub/mark-all-read", headers=headers)
    assert res_mark.status_code == status.HTTP_200_OK
    res_data = res_mark.json()
    assert res_data["marked_count"] == 3

    # Confirm all are now read
    unread_res = client.get("/api/v1/notifications/hub?is_read=false", headers=headers)
    assert unread_res.status_code == status.HTTP_200_OK
    assert unread_res.json() == []


def test_delete_notification_endpoint(client: TestClient, test_db: DBSession):
    """Verify DELETE /api/v1/notifications/hub/{id} removes notification and returns HTTP 204 No Content."""
    user_id = str(uuid.uuid4())
    headers = {"X-User-ID": user_id}

    n1 = create_notification(test_db, user_id, "SLA", "Delete Me", "To be deleted")

    res_delete = client.delete(f"/api/v1/notifications/hub/{n1.id}", headers=headers)
    assert res_delete.status_code == status.HTTP_204_NO_CONTENT
    assert res_delete.content == b""

    # Verify notification no longer exists
    remaining = get_user_notifications(test_db, user_id)
    assert len(remaining) == 0

    # Verify 404 on deleting non-existent notification
    res_404 = client.delete(f"/api/v1/notifications/hub/{n1.id}", headers=headers)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND
