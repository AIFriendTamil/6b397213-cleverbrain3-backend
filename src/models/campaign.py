"""SQLAlchemy ORM model for Campaign entity."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.lead import Lead


class Campaign(Base):
    """Campaign ORM entity model mapping to the campaigns table."""

    __tablename__ = "campaigns"

    campaign_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    campaign_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    budget_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    spent_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    start_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    end_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    leads: Mapped[List["Lead"]] = relationship(
        "Lead",
        back_populates="campaign",
    )

    @property
    def id(self) -> str:
        """Alias property for campaign_id for standard entity primary key access."""
        return self.campaign_id
