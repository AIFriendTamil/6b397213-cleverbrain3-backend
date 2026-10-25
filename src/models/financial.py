"""SQLAlchemy ORM models for ArAgingSnapshot and ArrMetric entities."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ArAgingSnapshot(Base):
    """ArAgingSnapshot ORM entity model mapping to the ar_aging_snapshots table."""

    __tablename__ = "ar_aging_snapshots"

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
    current_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    bucket_30_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    bucket_60_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    bucket_90_plus_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    snapshot_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ArrMetric(Base):
    """ArrMetric ORM entity model mapping to the arr_metrics table."""

    __tablename__ = "arr_metrics"

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
    metric_period: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    arr_value: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USD",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
