"""SQLAlchemy ORM model for TicketEvent / audit log entity."""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.ticket import Ticket


class TicketEventType(str, enum.Enum):
    """Enumeration of ticket event and audit log entry types."""

    TICKET_CREATED = "TICKET_CREATED"
    STATUS_CHANGE = "STATUS_CHANGE"
    COMMENT = "COMMENT"
    ESCALATION_TRIGGERED = "ESCALATION_TRIGGERED"
    ATTACHMENT_ADDED = "ATTACHMENT_ADDED"
    CSAT_SUBMITTED = "CSAT_SUBMITTED"


class TicketEvent(Base):
    """TicketEvent ORM entity model mapping to the ticket_events table."""

    __tablename__ = "ticket_events"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_type: Mapped[TicketEventType] = mapped_column(
        Enum(TicketEventType),
        nullable=False,
    )
    content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    attachment_url: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )
    attachment_filename: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    ticket: Mapped["Ticket"] = relationship(
        "Ticket",
        back_populates="events",
    )

    def __repr__(self) -> str:
        return (
            f"<TicketEvent id={self.id!r} ticket_id={self.ticket_id!r} "
            f"event_type={self.event_type}>"
        )
