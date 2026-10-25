"""Unit tests for NotificationPreference and Notification ORM models."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.notification import (
    Notification,
    NotificationCategory,
    NotificationPreference,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_notification_preference_creation_and_defaults(test_session: Session):
    """Verify NotificationPreference creation, field mappings, and default channel values."""
    user_id = str(uuid.uuid4())

    pref = NotificationPreference(
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
    )
    test_session.add(pref)
    test_session.commit()

    retrieved = (
        test_session.query(NotificationPreference)
        .filter_by(user_id=user_id, category=NotificationCategory.FINANCIAL)
        .first()
    )
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.user_id == user_id
    assert retrieved.category == NotificationCategory.FINANCIAL
    assert retrieved.channel_email is True
    assert retrieved.channel_sms is False
    assert retrieved.channel_in_app is True
    assert retrieved.phone_number is None
    assert isinstance(retrieved.updated_at, datetime)


def test_notification_preference_channel_updates(test_session: Session):
    """Verify updating NotificationPreference channel toggles and phone number."""
    user_id = str(uuid.uuid4())

    pref = NotificationPreference(
        user_id=user_id,
        category=NotificationCategory.SLA,
        channel_email=False,
        channel_sms=True,
        channel_in_app=True,
        phone_number="+15551234567",
    )
    test_session.add(pref)
    test_session.commit()

    retrieved = (
        test_session.query(NotificationPreference)
        .filter_by(user_id=user_id, category=NotificationCategory.SLA)
        .first()
    )
    assert retrieved is not None
    assert retrieved.channel_email is False
    assert retrieved.channel_sms is True
    assert retrieved.channel_in_app is True
    assert retrieved.phone_number == "+15551234567"


def test_notification_preference_unique_constraint(test_session: Session):
    """Verify unique constraint on (user_id, category) prevents duplicate preference rows."""
    user_id = str(uuid.uuid4())

    pref1 = NotificationPreference(
        user_id=user_id,
        category=NotificationCategory.MILESTONE,
    )
    test_session.add(pref1)
    test_session.commit()

    pref2 = NotificationPreference(
        user_id=user_id,
        category=NotificationCategory.MILESTONE,
    )
    test_session.add(pref2)
    with pytest.raises(IntegrityError):
        test_session.commit()


def test_notification_creation_and_defaults(test_session: Session):
    """Verify Notification creation, field mappings, defaults, and unread status."""
    user_id = str(uuid.uuid4())

    notification = Notification(
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
        title="Invoice Overdue",
        message="Invoice #INV-2026-001 is now overdue.",
    )
    test_session.add(notification)
    test_session.commit()

    retrieved = (
        test_session.query(Notification)
        .filter_by(user_id=user_id)
        .first()
    )
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.user_id == user_id
    assert retrieved.category == NotificationCategory.FINANCIAL
    assert retrieved.title == "Invoice Overdue"
    assert retrieved.message == "Invoice #INV-2026-001 is now overdue."
    assert retrieved.is_read is False
    assert isinstance(retrieved.created_at, datetime)


def test_notification_read_toggle(test_session: Session):
    """Verify updating is_read status on a Notification record."""
    user_id = str(uuid.uuid4())

    notification = Notification(
        user_id=user_id,
        category=NotificationCategory.SLA,
        title="SLA Breach Warning",
        message="Ticket #TCK-1002 response time SLA threshold breached.",
    )
    test_session.add(notification)
    test_session.commit()

    retrieved = test_session.query(Notification).filter_by(id=notification.id).first()
    assert retrieved is not None
    assert retrieved.is_read is False

    retrieved.is_read = True
    test_session.commit()

    updated = test_session.query(Notification).filter_by(id=notification.id).first()
    assert updated is not None
    assert updated.is_read is True
