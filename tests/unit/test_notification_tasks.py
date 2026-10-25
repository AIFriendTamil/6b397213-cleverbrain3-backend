"""Unit tests for Celery background worker tasks in notification_tasks.py."""

import uuid
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from src.connectors.email_adapter import EmailAdapter, EmailGatewayUnavailableError
from src.connectors.sms_adapter import SMSAdapter
from src.models.base import Base, create_db_engine
from src.models.notification import Notification, NotificationCategory, NotificationPreference
from src.services.notifications.preference import update_user_notification_preference
from src.workers.notification_tasks import (
    batch_dispatch_notifications_task,
    dispatch_notification_task,
    send_notification_task,
)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for worker task tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_dispatch_notification_task_success(test_db_session: Session):
    """Test successful dispatch task execution creating in-app alert and checking preferences (FR-8, FR-10)."""
    user_id = str(uuid.uuid4())

    mock_task_self = MagicMock()
    mock_task_self.request.retries = 0

    result = dispatch_notification_task(
        self=mock_task_self,
        user_id=user_id,
        category="Financial",
        title="Invoice Issued",
        message="Your new invoice #INV-1001 is ready.",
        email="user@example.com",
        phone_number="+14155552671",
        correlation_id="test-cid-999",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["correlation_id"] == "test-cid-999"
    assert result["user_id"] == user_id
    assert result["category"] == "Financial"

    # Verify In-App alert was created in central hub with timestamp and unread status (FR-8)
    notifications = test_db_session.query(Notification).filter_by(user_id=user_id).all()
    assert len(notifications) == 1
    notif = notifications[0]
    assert notif.title == "Invoice Issued"
    assert notif.message == "Your new invoice #INV-1001 is ready."
    assert notif.category == NotificationCategory.FINANCIAL
    assert notif.is_read is False
    assert notif.created_at is not None

    # Verify SMS was opted out by default preference setting (FR-10)
    sms_res = next(r for r in result["results"] if r["channel"] == "sms")
    assert sms_res["status"] == "opted_out"


def test_dispatch_notification_task_sla_alert(test_db_session: Session):
    """Test SLA alert trigger dispatch appearing in central hub with unread status (FR-8)."""
    user_id = str(uuid.uuid4())

    mock_task_self = MagicMock()
    mock_task_self.request.retries = 0

    result = dispatch_notification_task(
        self=mock_task_self,
        user_id=user_id,
        category=NotificationCategory.SLA,
        title="SLA Breach Warning",
        message="Ticket P1 #T-505 has reached 80% SLA elapsed time.",
        correlation_id="sla-alert-cid",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"

    notifications = (
        test_db_session.query(Notification)
        .filter_by(user_id=user_id, category=NotificationCategory.SLA)
        .all()
    )
    assert len(notifications) == 1
    sla_notif = notifications[0]
    assert sla_notif.title == "SLA Breach Warning"
    assert sla_notif.is_read is False


def test_dispatch_notification_task_channel_preferences_opt_out(test_db_session: Session):
    """Test disabling channel dispatches when opted out by user preference (FR-10)."""
    user_id = str(uuid.uuid4())

    # User opts out of SMS channel for Financial alerts
    update_user_notification_preference(
        db=test_db_session,
        user_id=user_id,
        category="Financial",
        channel_email=True,
        channel_sms=False,
        channel_in_app=True,
        phone_number="+14155552671",
    )

    mock_email_adapter = MagicMock(spec=EmailAdapter)
    mock_email_adapter.send_email.return_value = True

    mock_sms_adapter = MagicMock(spec=SMSAdapter)

    mock_task_self = MagicMock()
    mock_task_self.request.retries = 0

    result = dispatch_notification_task(
        self=mock_task_self,
        user_id=user_id,
        category="Financial",
        title="Payment Confirmation",
        message="Payment received.",
        email="user@example.com",
        phone_number="+14155552671",
        db_session=test_db_session,
        email_adapter=mock_email_adapter,
        sms_adapter=mock_sms_adapter,
    )

    assert result["status"] == "completed"

    # SMS sender should NOT be called because user opted out
    mock_sms_adapter.send_sms.assert_not_called()

    # Email sender SHOULD be called
    mock_email_adapter.send_email.assert_called_once()


def test_dispatch_notification_task_retry_exponential_backoff(test_db_session: Session):
    """Test exponential backoff retry handling when gateway raises an exception."""
    user_id = str(uuid.uuid4())

    update_user_notification_preference(
        db=test_db_session,
        user_id=user_id,
        category="Financial",
        channel_email=True,
        channel_sms=False,
        channel_in_app=True,
    )

    mock_email_adapter = MagicMock(spec=EmailAdapter)
    mock_email_adapter.send_email.side_effect = EmailGatewayUnavailableError(
        "SMTP server connection timeout"
    )

    mock_task_self = MagicMock()
    mock_task_self.request.retries = 0
    mock_task_self.max_retries = 3
    mock_task_self.default_retry_delay = 2
    mock_task_self.retry.side_effect = Exception("CeleryRetryTriggered")

    with pytest.raises(Exception, match="CeleryRetryTriggered"):
        dispatch_notification_task(
            self=mock_task_self,
            user_id=user_id,
            category="Financial",
            title="Gateway Error Test",
            message="Test message",
            email="user@example.com",
            db_session=test_db_session,
            email_adapter=mock_email_adapter,
        )

    # Verify self.retry was called with exponential backoff countdown (2s on first retry)
    mock_task_self.retry.assert_called_once()
    _, kwargs = mock_task_self.retry.call_args
    assert kwargs["countdown"] == 2


def test_dispatch_notification_task_validation_error(test_db_session: Session):
    """Test handling of validation errors (e.g. empty user_id) without retry."""
    mock_task_self = MagicMock()

    result = dispatch_notification_task(
        self=mock_task_self,
        user_id="",
        category="Financial",
        title="Title",
        message="Message",
        db_session=test_db_session,
    )

    assert result["status"] == "failed"
    assert "Validation error" in result["error"]
    mock_task_self.retry.assert_not_called()


def test_send_notification_task_alias(test_db_session: Session):
    """Test send_notification_task alias delegates to dispatch_notification_task."""
    user_id = str(uuid.uuid4())
    mock_task_self = MagicMock()
    mock_task_self.request.retries = 0

    result = send_notification_task(
        self=mock_task_self,
        user_id=user_id,
        category="Financial",
        title="Alias Test",
        message="Alias notification message.",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"


def test_batch_dispatch_notifications_task(test_db_session: Session):
    """Test batch_dispatch_notifications_task for multiple user notifications."""
    u1 = str(uuid.uuid4())
    u2 = str(uuid.uuid4())

    batch_data = [
        {
            "user_id": u1,
            "category": "Financial",
            "title": "Batch Item 1",
            "message": "First batch item",
        },
        {
            "user_id": u2,
            "category": "Milestone",
            "title": "Batch Item 2",
            "message": "Second batch item",
        },
    ]

    result = batch_dispatch_notifications_task(
        user_notifications=batch_data,
        correlation_id="batch-cid-100",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["total_count"] == 2
    assert result["completed_count"] == 2
    assert result["failed_count"] == 0
    assert len(result["items"]) == 2
