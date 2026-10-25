"""SQLAlchemy ORM models for Knowledge Base and Onboarding Center entities."""

import enum
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class KBContentType(str, enum.Enum):
    """Enumeration of knowledge base content types."""

    FAQ = "FAQ"
    PLAYBOOK = "PLAYBOOK"
    PROCESS_DOC = "PROCESS_DOC"
    TUTORIAL = "TUTORIAL"
    VIDEO = "VIDEO"


class KBCategory(Base):
    """KBCategory ORM entity model mapping to the kb_categories table."""

    __tablename__ = "kb_categories"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("kb_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    parent: Mapped[Optional["KBCategory"]] = relationship(
        "KBCategory",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[List["KBCategory"]] = relationship(
        "KBCategory",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    resources: Mapped[List["KBResource"]] = relationship(
        "KBResource",
        back_populates="category",
        cascade="all, delete-orphan",
    )


class KBResource(Base):
    """KBResource ORM entity model mapping to the kb_resources table."""

    __tablename__ = "kb_resources"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    category_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("kb_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    content_type: Mapped[KBContentType] = mapped_column(
        Enum(KBContentType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    body_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    media_url: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
    )
    tags: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    onboarding_phase: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    required_role: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    min_subscription_tier: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    published_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    category: Mapped["KBCategory"] = relationship(
        "KBCategory",
        back_populates="resources",
    )
    tutorial_steps: Mapped[List["KBTutorialStep"]] = relationship(
        "KBTutorialStep",
        back_populates="resource",
        cascade="all, delete-orphan",
        order_by="KBTutorialStep.step_number",
    )
    bookmarks: Mapped[List["KBBookmark"]] = relationship(
        "KBBookmark",
        back_populates="resource",
        cascade="all, delete-orphan",
    )
    user_progress: Mapped[List["KBUserProgress"]] = relationship(
        "KBUserProgress",
        back_populates="resource",
        cascade="all, delete-orphan",
    )


class KBTutorialStep(Base):
    """KBTutorialStep ORM entity model mapping to the kb_tutorial_steps table."""

    __tablename__ = "kb_tutorial_steps"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    resource_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("kb_resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    video_url: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
    )

    resource: Mapped["KBResource"] = relationship(
        "KBResource",
        back_populates="tutorial_steps",
    )


class KBBookmark(Base):
    """KBBookmark ORM entity model mapping to the kb_bookmarks table."""

    __tablename__ = "kb_bookmarks"
    __table_args__ = (
        UniqueConstraint("user_id", "resource_id", name="uq_kb_bookmarks_user_resource"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("kb_resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    resource: Mapped["KBResource"] = relationship(
        "KBResource",
        back_populates="bookmarks",
    )


class KBUserProgress(Base):
    """KBUserProgress ORM entity model mapping to the kb_user_progress table."""

    __tablename__ = "kb_user_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "resource_id", "step_number", name="uq_kb_user_progress_user_resource_step"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("kb_resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_number: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    last_accessed_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    resource: Mapped["KBResource"] = relationship(
        "KBResource",
        back_populates="user_progress",
    )
