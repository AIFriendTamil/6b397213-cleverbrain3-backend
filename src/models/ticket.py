"""SQLAlchemy ORM model for Ticket entity and status, severity, and escalation enums."""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.audit_log import TicketEvent
    from src.models.csat import CSATResponse


class TicketStatus(str, enum.Enum):
    """Enumeration of support ticket lifecycle status states."""

    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketSeverity(str, enum.Enum):
    """Enumeration of support ticket severity impact levels."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class EscalationTier(enum.IntEnum):
    """Enumeration of support ticket escalation tiers."""

    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3


class Ticket(Base):
    """Ticket ORM entity model mapping to the tickets table."""

    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    severity: Mapped[TicketSeverity] = mapped_column(
        Enum(TicketSeverity),
        nullable=False,
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus),
        nullable=False,
        default=TicketStatus.NEW,
        index=True,
    )
    escalation_tier: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=EscalationTier.TIER_1,
    )
    organization_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    creator_user_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    assigned_agent_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    response_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resolution_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    is_sla_breached: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    events: Mapped[List["TicketEvent"]] = relationship(
        "TicketEvent",
        back_populates="ticket",
        cascade="all, delete-orphan",
    )
    csat_response: Mapped[Optional["CSATResponse"]] = relationship(
        "CSATResponse",
        back_populates="ticket",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Ticket id={self.id!r} title={self.title!r} severity={self.severity} status={self.status}>"
        )
