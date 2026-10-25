"""SQLAlchemy ORM model for Invoice entity."""

import enum
import uuid
from datetime import date as date_type, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class PaymentStatus(str, enum.Enum):
    """Enumeration of invoice payment statuses."""

    UNPAID = "UNPAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    PARTIALLY_PAID = "PARTIALLY_PAID"


class Invoice(Base):
    """Invoice ORM entity model mapping to the invoices table."""

    __tablename__ = "invoices"

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
    invoice_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    issue_date: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
    )
    due_date: Mapped[date_type] = mapped_column(
        Date,
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=PaymentStatus.UNPAID,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
