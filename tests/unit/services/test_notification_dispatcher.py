"""Unit tests for notification channel evaluation, delivery routing, and PII-redacted logging."""

import uuid
import pytest
import logging
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.notification import (
    NotificationCategory,
    NotificationPreference,
)
from src.services.notifications.dispatcher import (
    ChannelDispatchResult,
    DispatchStatus,
    DispatchSummary,
    DispatcherValidationError,
    dispatch_notification,
    evaluate_notification_channels,
    redact_pii_for_logging,
)
from src.services.notifications.preference import (
    update_user_notification_preference,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_redact_pii_for_logging_sensitive_keys():
    """Verify sensitive PII dict keys are redacted for NFR-5 logging compliance."""
    sensitive_data = {
        "user_id": "usr-12345",
        "email": "john.doe@example.com",
        "phone_number": "+14155552671",
        "message": "Your invoice #1001 is ready for payment",
        "recipient_name": "John Doe",
        "category": "Financial",
    }

    sanitized = redact_pii_for_logging(sensitive_data)

    assert sanitized["user_id"] == "usr-12345"
    assert sanitized["category"] == "Financial"
    assert sanitized["email"] == "[REDACTED len=20]"
    assert sanitized["phone_number"] == "[REDACTED len=12]"
    assert sanitized["message"] == "[REDACTED len=38]"
    assert sanitized["recipient_name"] == "[REDACTED len=8]"


def test_redact_pii_for_logging_string_scrubbing():
    """Verify inline emails and phone numbers in raw strings are scrubbed."""
    raw_text = "Alert sent to user at john.doe@example.com via phone +14155552671 successfully."
    scrubbed = redact_pii_for_logging(raw_text)

    assert "john.doe@example.com" not in scrubbed
    assert "+14155552671" not in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed
    assert "[REDACTED_PHONE]" in scrubbed


def test_evaluate_notification_channels_default(test_session: Session):
    """Verify default channel evaluation flags (Email & In-App enabled, SMS disabled)."""
    user_id = str(uuid.uuid4())

    flags = evaluate_notification_channels(test_session, user_id, "Financial")

    assert flags == {
        "email": True,
        "sms": False,
        "in_app": True,
    }


def test_evaluate_notification_channels_validation_error(test_session: Session):
    """Verify validation error raised for empty user_id or invalid category."""
    with pytest.raises(DispatcherValidationError):
        evaluate_notification_channels(test_session, "", "Financial")

    with pytest.raises(DispatcherValidationError):
        evaluate_notification_channels(test_session, str(uuid.uuid4()), "INVALID_CAT")


def test_dispatch_notification_default_preferences(test_session: Session):
    """Verify default dispatch behavior (FR-10: SMS opted out by default)."""
    user_id = str(uuid.uuid4())

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="Financial",
        title="Payment Reminder",
        message="Your quarterly invoice is due.",
    )

    assert summary.user_id == user_id
    assert summary.category == NotificationCategory.FINANCIAL
    assert summary.is_any_dispatched() is True

    # In-app should be dispatched
    assert summary.results["in_app"].status == DispatchStatus.DISPATCHED
    assert summary.results["in_app"].notification_id is not None

    # Email should be dispatched (enabled)
    assert summary.results["email"].status == DispatchStatus.DISPATCHED

    # SMS should be opted out by default (FR-10)
    assert summary.results["sms"].status == DispatchStatus.OPTED_OUT


def test_dispatch_notification_sms_enabled_with_phone(test_session: Session):
    """Verify enabling SMS in preferences routes SMS dispatch successfully."""
    user_id = str(uuid.uuid4())

    # Enable SMS and set valid E.164 phone number
    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="SLA",
        channel_sms=True,
        phone_number="+14155552671",
    )

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="SLA",
        title="SLA Warning",
        message="Ticket #402 is nearing SLA breach threshold.",
    )

    assert summary.results["sms"].status == DispatchStatus.DISPATCHED
    assert summary.results["email"].status == DispatchStatus.DISPATCHED
    assert summary.results["in_app"].status == DispatchStatus.DISPATCHED


def test_dispatch_notification_all_opted_out(test_session: Session):
    """Verify disabling all channels results in OPTED_OUT across all channels."""
    user_id = str(uuid.uuid4())

    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="Milestone",
        channel_email=False,
        channel_sms=False,
        channel_in_app=False,
    )

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="Milestone",
        title="Milestone Reached",
        message="Sprint 4 completed.",
    )

    assert summary.is_all_opted_out() is True
    assert summary.results["email"].status == DispatchStatus.OPTED_OUT
    assert summary.results["sms"].status == DispatchStatus.OPTED_OUT
    assert summary.results["in_app"].status == DispatchStatus.OPTED_OUT


def test_dispatch_notification_force_in_app(test_session: Session):
    """Verify force_in_app=True creates in-app notification even if in-app channel is opted out."""
    user_id = str(uuid.uuid4())

    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="SLA",
        channel_email=False,
        channel_sms=False,
        channel_in_app=False,
    )

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="SLA",
        title="Critical SLA Breach",
        message="Ticket escalated.",
        force_in_app=True,
    )

    assert summary.results["in_app"].status == DispatchStatus.DISPATCHED
    assert summary.results["in_app"].notification_id is not None
    assert summary.results["email"].status == DispatchStatus.OPTED_OUT
    assert summary.results["sms"].status == DispatchStatus.OPTED_OUT


def test_dispatch_notification_sender_callbacks(test_session: Session):
    """Verify email_sender and sms_sender callbacks are executed and handle failures gracefully."""
    user_id = str(uuid.uuid4())

    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="Financial",
        channel_email=True,
        channel_sms=True,
        phone_number="+14155552671",
    )

    sent_emails = []
    sent_sms = []

    def mock_email_sender(to_email: str, subject: str, body: str) -> bool:
        sent_emails.append((to_email, subject, body))
        return True

    def mock_sms_sender(to_phone: str, body: str) -> bool:
        sent_sms.append((to_phone, body))
        return True

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="Financial",
        title="Invoice Issued",
        message="Invoice #1002 issued.",
        email="user@example.com",
        phone_number="+14155552671",
        email_sender=mock_email_sender,
        sms_sender=mock_sms_sender,
    )

    assert len(sent_emails) == 1
    assert sent_emails[0][0] == "user@example.com"
    assert sent_emails[0][1] == "Invoice Issued"

    assert len(sent_sms) == 1
    assert sent_sms[0][0] == "+14155552671"

    assert summary.results["email"].status == DispatchStatus.DISPATCHED
    assert summary.results["sms"].status == DispatchStatus.DISPATCHED


def test_dispatch_notification_callback_failure(test_session: Session):
    """Verify gateway adapter exceptions are caught and recorded as FAILED."""
    user_id = str(uuid.uuid4())

    update_user_notification_preference(
        db=test_session,
        user_id=user_id,
        category="Financial",
        channel_email=True,
        channel_sms=True,
        phone_number="+14155552671",
    )

    def failing_email_sender(to_email: str, subject: str, body: str) -> bool:
        raise RuntimeError("SMTP connection timeout")

    def failing_sms_sender(to_phone: str, body: str) -> bool:
        return False

    summary = dispatch_notification(
        db=test_session,
        user_id=user_id,
        category="Financial",
        title="Payment Failed",
        message="Payment processing error.",
        email="user@example.com",
        phone_number="+14155552671",
        email_sender=failing_email_sender,
        sms_sender=failing_sms_sender,
    )

    assert summary.results["email"].status == DispatchStatus.FAILED
    assert "SMTP connection timeout" in summary.results["email"].reason

    assert summary.results["sms"].status == DispatchStatus.FAILED
    assert "returned failure status" in summary.results["sms"].reason


def test_dispatch_notification_validation_errors(test_session: Session):
    """Verify validation errors raised for empty parameters."""
    user_id = str(uuid.uuid4())

    with pytest.raises(DispatcherValidationError):
        dispatch_notification(test_session, "", "Financial", "Title", "Message")

    with pytest.raises(DispatcherValidationError):
        dispatch_notification(test_session, user_id, "Financial", "", "Message")

    with pytest.raises(DispatcherValidationError):
        dispatch_notification(test_session, user_id, "Financial", "Title", "")

    with pytest.raises(DispatcherValidationError):
        dispatch_notification(test_session, user_id, "INVALID_CAT", "Title", "Message")


def test_logging_pii_redaction_during_dispatch(test_session: Session, caplog):
    """Verify logger output during dispatch contains no raw PII (email, phone, message payload)."""
    user_id = str(uuid.uuid4())

    raw_email = "secret.user@example.com"
    raw_phone = "+14155552671"
    raw_message = "Confidential financial statement details for Q3"

    with caplog.at_level(logging.INFO):
        dispatch_notification(
            db=test_session,
            user_id=user_id,
            category="Financial",
            title="Financial Alert",
            message=raw_message,
            email=raw_email,
            phone_number=raw_phone,
        )

    log_text = caplog.text

    # Confirm PII values are absent from log text
    assert raw_email not in log_text
    assert raw_phone not in log_text
    assert raw_message not in log_text
