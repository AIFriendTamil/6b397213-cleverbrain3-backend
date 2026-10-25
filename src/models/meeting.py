"""SQLAlchemy ORM models for ScheduledMeeting and MeetingBrief entities."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.lead import Lead


class ScheduledMeeting(Base):
    """ScheduledMeeting ORM entity model mapping to the scheduled_meetings table."""

    __tablename__ = "scheduled_meetings"

    meeting_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    lead_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("leads.lead_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title_text: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    start_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    end_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    calendar_event_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    brief_summary_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    lead: Mapped["Lead"] = relationship(
        "Lead",
        back_populates="meetings",
    )
    brief: Mapped[Optional["MeetingBrief"]] = relationship(
        "MeetingBrief",
        back_populates="meeting",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def id(self) -> str:
        """Alias property for meeting_id for standard entity primary key access."""
        return self.meeting_id

    def __repr__(self) -> str:
        return f"<ScheduledMeeting meeting_id={self.meeting_id} title_text={self.title_text!r}>"


class MeetingBrief(Base):
    """MeetingBrief ORM entity model mapping to the meeting_briefs table."""

    __tablename__ = "meeting_briefs"

    brief_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    meeting_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("scheduled_meetings.meeting_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    lead_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("leads.lead_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quality_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    interaction_history_summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    key_discussion_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    brief_summary_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    meeting: Mapped[ScheduledMeeting] = relationship(
        "ScheduledMeeting",
        back_populates="brief",
    )
    lead: Mapped["Lead"] = relationship(
        "Lead",
    )

    @property
    def id(self) -> str:
        """Alias property for brief_id for standard entity primary key access."""
        return self.brief_id

    def __repr__(self) -> str:
        return f"<MeetingBrief brief_id={self.brief_id} meeting_id={self.meeting_id}>"
