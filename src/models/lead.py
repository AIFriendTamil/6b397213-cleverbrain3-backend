"""SQLAlchemy ORM models for Lead and LeadSource entities."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.campaign import Campaign
    from src.models.meeting import ScheduledMeeting


class LeadSource(Base):
    """LeadSource ORM entity model mapping to the lead_sources table."""

    __tablename__ = "lead_sources"

    source_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    source_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    leads: Mapped[List["Lead"]] = relationship(
        "Lead",
        back_populates="source",
        cascade="all, delete-orphan",
    )

    @property
    def id(self) -> str:
        """Alias property for source_id for standard entity primary key access."""
        return self.source_id


class Lead(Base):
    """Lead ORM entity model mapping to the leads table."""

    __tablename__ = "leads"

    lead_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("lead_sources.source_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("campaigns.campaign_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    quality_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    status_stage: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="NEW",
    )
    state_code: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
    )
    postal_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )
    region_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="Unmapped Region",
    )
    deal_value: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    source: Mapped[LeadSource] = relationship(
        "LeadSource",
        back_populates="leads",
    )
    campaign: Mapped[Optional["Campaign"]] = relationship(
        "Campaign",
        back_populates="leads",
    )
    meetings: Mapped[List["ScheduledMeeting"]] = relationship(
        "ScheduledMeeting",
        back_populates="lead",
        cascade="all, delete-orphan",
    )

    @property
    def id(self) -> str:
        """Alias property for lead_id for standard entity primary key access."""
        return self.lead_id

    def __init__(self, **kwargs):
        if "region_name" not in kwargs or not kwargs["region_name"] or not str(kwargs["region_name"]).strip():
            kwargs["region_name"] = "Unmapped Region"
        super().__init__(**kwargs)
