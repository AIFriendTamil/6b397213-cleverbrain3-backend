"""Domain service for in-app notification creation, querying, read state updates, and deletion."""

from datetime import datetime, timezone
import logging
from typing import List, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.notification import (
    Notification,
    NotificationCategory,
)
from src.services.notifications.preference import (
    PreferenceValidationError,
    parse_notification_category,
)

logger = logging.getLogger(__name__)


class NotificationHubError(Exception):
    """Base exception class for notification hub domain service operations."""

    pass


class NotificationNotFoundError(NotificationHubError):
    """Raised when a requested notification record cannot be found."""

    pass


class NotificationValidationError(NotificationHubError):
    """Raised when notification inputs or parameters fail validation."""

    pass


def _parse_category(
    category_input: Union[NotificationCategory, str]
) -> NotificationCategory:
    """Parse and validate category input into a NotificationCategory enum value.

    Args:
        category_input: NotificationCategory enum or string value.

    Returns:
        NotificationCategory enum value.

    Raises:
        NotificationValidationError: If category input is invalid or empty.
    """
    try:
        return parse_notification_category(category_input)
    except PreferenceValidationError as err:
        raise NotificationValidationError(str(err)) from err


def create_notification(
    db: Session,
    user_id: str,
    category: Union[NotificationCategory, str],
    title: str,
    message: str,
) -> Notification:
    """Create and persist a new in-app notification for a user.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: NotificationCategory enum or string representation.
        title: Notification summary header string.
        message: Detailed notification message payload.

    Returns:
        Persisted Notification ORM instance.

    Raises:
        NotificationValidationError: If required fields are missing or invalid.
    """
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise NotificationValidationError("User ID cannot be empty")

    if not title or not isinstance(title, str) or not title.strip():
        raise NotificationValidationError("Notification title cannot be empty")

    if not message or not isinstance(message, str) or not message.strip():
        raise NotificationValidationError("Notification message cannot be empty")

    category_enum = _parse_category(category)

    notification = Notification(
        user_id=user_id.strip(),
        category=category_enum,
        title=title.strip(),
        message=message.strip(),
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )

    db.add(notification)
    db.commit()
    db.refresh(notification)

    logger.info(
        "Created in-app notification %s for user %s in category %s",
        notification.id,
        user_id,
        category_enum.value,
    )

    return notification


def get_notification_by_id(
    db: Session,
    notification_id: str,
    user_id: Optional[str] = None,
) -> Notification:
    """Retrieve a single notification by its primary key ID, optionally scoped to a user.

    Args:
        db: Synchronous SQLAlchemy database session.
        notification_id: Notification UUID string.
        user_id: Optional user identifier for ownership verification.

    Returns:
        Notification ORM entity.

    Raises:
        NotificationValidationError: If notification_id is invalid.
        NotificationNotFoundError: If the notification is not found.
    """
    if not notification_id or not isinstance(notification_id, str) or not notification_id.strip():
        raise NotificationValidationError("Notification ID cannot be empty")

    stmt = select(Notification).where(Notification.id == notification_id.strip())
    if user_id and isinstance(user_id, str) and user_id.strip():
        stmt = stmt.where(Notification.user_id == user_id.strip())

    notification = db.scalars(stmt).first()
    if not notification:
        msg = f"Notification with ID '{notification_id}' not found"
        if user_id:
            msg += f" for user '{user_id}'"
        raise NotificationNotFoundError(msg)

    return notification


def get_user_notifications(
    db: Session,
    user_id: str,
    category: Optional[Union[NotificationCategory, str]] = None,
    is_read: Optional[bool] = None,
) -> List[Notification]:
    """Retrieve notifications for a specific user with optional category and read state filtering.

    Notifications are ordered by creation timestamp in descending order (newest first).

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: Optional category filter.
        is_read: Optional read status boolean filter.

    Returns:
        List of matching Notification ORM entities.

    Raises:
        NotificationValidationError: If user_id is missing or category filter is invalid.
    """
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise NotificationValidationError("User ID cannot be empty")

    stmt = select(Notification).where(Notification.user_id == user_id.strip())

    if category is not None:
        category_enum = _parse_category(category)
        stmt = stmt.where(Notification.category == category_enum)

    if is_read is not None:
        stmt = stmt.where(Notification.is_read == bool(is_read))

    stmt = stmt.order_by(Notification.created_at.desc())

    return list(db.scalars(stmt).all())


def get_unread_notification_count(
    db: Session,
    user_id: str,
    category: Optional[Union[NotificationCategory, str]] = None,
) -> int:
    """Get the total count of unread notifications for a user.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: Optional category filter.

    Returns:
        Integer count of unread notifications.

    Raises:
        NotificationValidationError: If user_id is missing or category filter is invalid.
    """
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise NotificationValidationError("User ID cannot be empty")

    stmt = select(Notification).where(
        Notification.user_id == user_id.strip(),
        Notification.is_read.is_(False),
    )

    if category is not None:
        category_enum = _parse_category(category)
        stmt = stmt.where(Notification.category == category_enum)

    return len(list(db.scalars(stmt).all()))


def update_notification_read_status(
    db: Session,
    notification_id: str,
    is_read: bool,
    user_id: Optional[str] = None,
) -> Notification:
    """Update the read/unread state of a specific notification record.

    Args:
        db: Synchronous SQLAlchemy database session.
        notification_id: Notification UUID string.
        is_read: Target read status boolean.
        user_id: Optional user identifier for ownership verification.

    Returns:
        Updated Notification ORM entity.

    Raises:
        NotificationValidationError: If parameters fail validation.
        NotificationNotFoundError: If the notification record is not found.
    """
    notification = get_notification_by_id(db, notification_id, user_id=user_id)
    notification.is_read = bool(is_read)

    db.commit()
    db.refresh(notification)

    logger.info(
        "Updated notification %s read state to %s",
        notification_id,
        notification.is_read,
    )

    return notification


def mark_all_notifications_as_read(
    db: Session,
    user_id: str,
    category: Optional[Union[NotificationCategory, str]] = None,
) -> int:
    """Mark all unread notifications as read for a given user.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: Optional category filter to constrain bulk mark operation.

    Returns:
        Integer count of notifications updated to read status.

    Raises:
        NotificationValidationError: If user_id is missing or category filter is invalid.
    """
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise NotificationValidationError("User ID cannot be empty")

    stmt = select(Notification).where(
        Notification.user_id == user_id.strip(),
        Notification.is_read.is_(False),
    )

    if category is not None:
        category_enum = _parse_category(category)
        stmt = stmt.where(Notification.category == category_enum)

    unread_notifications = list(db.scalars(stmt).all())
    marked_count = len(unread_notifications)

    if marked_count > 0:
        for notification in unread_notifications:
            notification.is_read = True
        db.commit()

    logger.info(
        "Marked %d unread notifications as read for user %s",
        marked_count,
        user_id,
    )

    return marked_count


def delete_notification(
    db: Session,
    notification_id: str,
    user_id: Optional[str] = None,
) -> bool:
    """Delete an in-app notification record by ID.

    Args:
        db: Synchronous SQLAlchemy database session.
        notification_id: Notification UUID string.
        user_id: Optional user identifier for ownership verification.

    Returns:
        True if the notification was successfully deleted.

    Raises:
        NotificationValidationError: If parameters fail validation.
        NotificationNotFoundError: If the notification record is not found.
    """
    notification = get_notification_by_id(db, notification_id, user_id=user_id)

    db.delete(notification)
    db.commit()

    logger.info("Deleted notification %s", notification_id)

    return True
