"""SQLAlchemy ORM model for CSATResponse entity."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.ticket import Ticket


class CSATResponse(Base):
    """CSATResponse ORM entity model mapping to the csat_responses table."""

    __tablename__ = "csat_responses"
    __table_args__ = (
        CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="check_csat_rating_range",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    feedback_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    ticket: Mapped["Ticket"] = relationship(
        "Ticket",
        back_populates="csat_response",
    )

    def __repr__(self) -> str:
        return (
            f"<CSATResponse id={self.id!r} ticket_id={self.ticket_id!r} "
            f"rating={self.rating} user_id={self.user_id!r}>"
        )
