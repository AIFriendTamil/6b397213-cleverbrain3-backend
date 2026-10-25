"""SQLAlchemy ORM models for Quote and QuoteApproval entities."""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class QuoteStatus(str, enum.Enum):
    """Enumeration of quote lifecycle statuses."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class QuoteApprovalStatus(str, enum.Enum):
    """Enumeration of quote approval decision statuses."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Quote(Base):
    """Quote ORM entity model mapping to the quotes table."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    account_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(
            QuoteStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=QuoteStatus.DRAFT,
        index=True,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
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

    approvals: Mapped[List["QuoteApproval"]] = relationship(
        "QuoteApproval",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteApproval.step_number",
    )


class QuoteApproval(Base):
    """QuoteApproval ORM entity model mapping to the quote_approvals table."""

    __tablename__ = "quote_approvals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    quote_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approver_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    step_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    status: Mapped[QuoteApprovalStatus] = mapped_column(
        Enum(
            QuoteApprovalStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=QuoteApprovalStatus.PENDING,
        index=True,
    )
    comment: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    quote: Mapped["Quote"] = relationship(
        "Quote",
        back_populates="approvals",
    )
