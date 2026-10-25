"""Unit tests for in-app notification creation, querying, read state updates, and deletion domain service."""

import uuid
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.notification import (
    Notification,
    NotificationCategory,
)
from src.services.notifications.hub import (
    NotificationNotFoundError,
    NotificationValidationError,
    create_notification,
    delete_notification,
    get_notification_by_id,
    get_unread_notification_count,
    get_user_notifications,
    mark_all_notifications_as_read,
    update_notification_read_status,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_create_notification_success(test_session: Session):
    """Verify creating an in-app notification with valid parameters."""
    user_id = str(uuid.uuid4())

    notification = create_notification(
        db=test_session,
        user_id=user_id,
        category="SLA",
        title="SLA Breach Warning",
        message="Response time SLA threshold breached for Ticket #TCK-8812.",
    )

    assert notification is not None
    assert notification.id is not None
    assert notification.user_id == user_id
    assert notification.category == NotificationCategory.SLA
    assert notification.title == "SLA Breach Warning"
    assert notification.message == "Response time SLA threshold breached for Ticket #TCK-8812."
    assert notification.is_read is False
    assert notification.created_at is not None

    # Verify query back from database
    retrieved = test_session.query(Notification).filter_by(id=notification.id).first()
    assert retrieved is not None
    assert retrieved.user_id == user_id


def test_create_notification_category_enum(test_session: Session):
    """Verify creating a notification using NotificationCategory enum."""
    user_id = str(uuid.uuid4())

    notification = create_notification(
        db=test_session,
        user_id=user_id,
        category=NotificationCategory.FINANCIAL,
        title="Invoice Issued",
        message="Invoice #INV-9001 issued.",
    )

    assert notification.category == NotificationCategory.FINANCIAL


def test_create_notification_validation_errors(test_session: Session):
    """Verify create_notification raises NotificationValidationError on invalid inputs."""
    user_id = str(uuid.uuid4())

    # Empty user_id
    with pytest.raises(NotificationValidationError):
        create_notification(test_session, "", "SLA", "Title", "Message")

    # Empty title
    with pytest.raises(NotificationValidationError):
        create_notification(test_session, user_id, "SLA", "", "Message")

    # Empty message
    with pytest.raises(NotificationValidationError):
        create_notification(test_session, user_id, "SLA", "Title", "   ")

    # Invalid category
    with pytest.raises(NotificationValidationError):
        create_notification(test_session, user_id, "INVALID_CATEGORY", "Title", "Message")


def test_get_notification_by_id(test_session: Session):
    """Verify retrieving a notification by ID and user scoping."""
    user_id_1 = str(uuid.uuid4())
    user_id_2 = str(uuid.uuid4())

    n1 = create_notification(test_session, user_id_1, "Milestone", "Title 1", "Message 1")

    # Retrieval by ID
    found = get_notification_by_id(test_session, n1.id)
    assert found.id == n1.id

    # Retrieval with matching user_id
    found_user = get_notification_by_id(test_session, n1.id, user_id=user_id_1)
    assert found_user.id == n1.id

    # Retrieval with non-matching user_id raises NotFoundError
    with pytest.raises(NotificationNotFoundError):
        get_notification_by_id(test_session, n1.id, user_id=user_id_2)

    # Retrieval with non-existent ID
    with pytest.raises(NotificationNotFoundError):
        get_notification_by_id(test_session, str(uuid.uuid4()))

    # Empty ID raises ValidationError
    with pytest.raises(NotificationValidationError):
        get_notification_by_id(test_session, "")


def test_get_user_notifications_filtering_and_ordering(test_session: Session):
    """Verify querying notifications with category, read status filters and ordering."""
    user_id = str(uuid.uuid4())

    n1 = create_notification(test_session, user_id, "Financial", "Invoice 1", "Msg 1")
    n2 = create_notification(test_session, user_id, "SLA", "SLA 1", "Msg 2")
    n3 = create_notification(test_session, user_id, "Financial", "Invoice 2", "Msg 3")

    # Mark n1 as read
    update_notification_read_status(test_session, n1.id, is_read=True)

    # Fetch all for user
    all_notifs = get_user_notifications(test_session, user_id)
    assert len(all_notifs) == 3
    # Ordered newest first (n3, n2, n1)
    assert [n.id for n in all_notifs] == [n3.id, n2.id, n1.id]

    # Category filter
    financial_notifs = get_user_notifications(test_session, user_id, category="Financial")
    assert len(financial_notifs) == 2
    assert {n.id for n in financial_notifs} == {n1.id, n3.id}

    sla_notifs = get_user_notifications(test_session, user_id, category="SLA")
    assert len(sla_notifs) == 1
    assert sla_notifs[0].id == n2.id

    # Read status filter
    unread_notifs = get_user_notifications(test_session, user_id, is_read=False)
    assert len(unread_notifs) == 2
    assert {n.id for n in unread_notifs} == {n2.id, n3.id}

    read_notifs = get_user_notifications(test_session, user_id, is_read=True)
    assert len(read_notifs) == 1
    assert read_notifs[0].id == n1.id


def test_get_user_notifications_validation_error(test_session: Session):
    """Verify get_user_notifications raises validation error for invalid parameters."""
    with pytest.raises(NotificationValidationError):
        get_user_notifications(test_session, "")

    with pytest.raises(NotificationValidationError):
        get_user_notifications(test_session, "user123", category="INVALID")


def test_get_unread_notification_count(test_session: Session):
    """Verify calculating unread notification counts for a user."""
    user_id = str(uuid.uuid4())

    assert get_unread_notification_count(test_session, user_id) == 0

    n1 = create_notification(test_session, user_id, "Financial", "Title 1", "Msg 1")
    n2 = create_notification(test_session, user_id, "SLA", "Title 2", "Msg 2")
    n3 = create_notification(test_session, user_id, "Financial", "Title 3", "Msg 3")

    assert get_unread_notification_count(test_session, user_id) == 3
    assert get_unread_notification_count(test_session, user_id, category="Financial") == 2
    assert get_unread_notification_count(test_session, user_id, category="SLA") == 1

    # Mark n1 as read
    update_notification_read_status(test_session, n1.id, is_read=True)

    assert get_unread_notification_count(test_session, user_id) == 2
    assert get_unread_notification_count(test_session, user_id, category="Financial") == 1


def test_update_notification_read_status(test_session: Session):
    """Verify toggling read status on a notification record."""
    user_id = str(uuid.uuid4())
    n = create_notification(test_session, user_id, "Milestone", "Title", "Message")
    assert n.is_read is False

    # Mark as read
    updated = update_notification_read_status(test_session, n.id, is_read=True, user_id=user_id)
    assert updated.is_read is True

    # Mark back as unread
    updated_unread = update_notification_read_status(test_session, n.id, is_read=False, user_id=user_id)
    assert updated_unread.is_read is False


def test_mark_all_notifications_as_read(test_session: Session):
    """Verify bulk marking all unread notifications as read."""
    user_id = str(uuid.uuid4())

    create_notification(test_session, user_id, "Financial", "Title 1", "Msg 1")
    create_notification(test_session, user_id, "SLA", "Title 2", "Msg 2")
    create_notification(test_session, user_id, "Milestone", "Title 3", "Msg 3")

    assert get_unread_notification_count(test_session, user_id) == 3

    # Bulk mark as read
    count = mark_all_notifications_as_read(test_session, user_id)
    assert count == 3
    assert get_unread_notification_count(test_session, user_id) == 0

    # Subsequent mark all read returns 0
    count_again = mark_all_notifications_as_read(test_session, user_id)
    assert count_again == 0


def test_mark_all_notifications_as_read_with_category_filter(test_session: Session):
    """Verify bulk marking unread notifications as read filtered by category."""
    user_id = str(uuid.uuid4())

    create_notification(test_session, user_id, "Financial", "Title 1", "Msg 1")
    create_notification(test_session, user_id, "Financial", "Title 2", "Msg 2")
    create_notification(test_session, user_id, "SLA", "Title 3", "Msg 3")

    # Mark only Financial as read
    count = mark_all_notifications_as_read(test_session, user_id, category="Financial")
    assert count == 2

    assert get_unread_notification_count(test_session, user_id, category="Financial") == 0
    assert get_unread_notification_count(test_session, user_id, category="SLA") == 1
    assert get_unread_notification_count(test_session, user_id) == 1


def test_delete_notification(test_session: Session):
    """Verify deleting a notification record by ID."""
    user_id = str(uuid.uuid4())
    n = create_notification(test_session, user_id, "Milestone", "Title", "Message")

    result = delete_notification(test_session, n.id, user_id=user_id)
    assert result is True

    # Confirm deletion
    with pytest.raises(NotificationNotFoundError):
        get_notification_by_id(test_session, n.id)


def test_delete_notification_not_found(test_session: Session):
    """Verify delete_notification raises error when notification doesn't exist or wrong user."""
    user_id_1 = str(uuid.uuid4())
    user_id_2 = str(uuid.uuid4())
    n = create_notification(test_session, user_id_1, "Milestone", "Title", "Message")

    with pytest.raises(NotificationNotFoundError):
        delete_notification(test_session, n.id, user_id=user_id_2)

    with pytest.raises(NotificationNotFoundError):
        delete_notification(test_session, str(uuid.uuid4()))
