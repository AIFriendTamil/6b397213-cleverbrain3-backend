"""SQLAlchemy ORM models for NotificationPreference and Notification entities."""

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class NotificationCategory(str, enum.Enum):
    """Enumeration of notification categories."""

    FINANCIAL = "Financial"
    MILESTONE = "Milestone"
    SLA = "SLA"


class NotificationPreference(Base):
    """NotificationPreference ORM entity model mapping to the notification_preferences table."""

    __tablename__ = "notification_preferences"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    category: Mapped[NotificationCategory] = mapped_column(
        Enum(
            NotificationCategory,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )
    channel_email: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    channel_sms: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    channel_in_app: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    phone_number: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "category",
            name="uq_notification_preference_user_category",
        ),
    )


class Notification(Base):
    """Notification ORM entity model mapping to the notifications table."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    category: Mapped[NotificationCategory] = mapped_column(
        Enum(
            NotificationCategory,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
