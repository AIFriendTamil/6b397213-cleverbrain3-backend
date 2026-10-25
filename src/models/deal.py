"""SQLAlchemy ORM models for Deal, DealStage, Milestone, CriticalTask, and Stakeholder entities."""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class MilestoneStatus(str, enum.Enum):
    """Status enumeration for deal milestones."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"


class DealStage(Base):
    """DealStage ORM entity model mapping to the deal_stages table."""

    __tablename__ = "deal_stages"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
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

    deals: Mapped[List["Deal"]] = relationship(
        "Deal",
        back_populates="stage",
        cascade="all, delete-orphan",
    )


class Deal(Base):
    """Deal ORM entity model mapping to the deals table."""

    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    monetary_value: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    stage_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deal_stages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_team_member_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    win_probability: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
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

    stage: Mapped["DealStage"] = relationship(
        "DealStage",
        back_populates="deals",
    )
    milestones: Mapped[List["Milestone"]] = relationship(
        "Milestone",
        back_populates="deal",
        cascade="all, delete-orphan",
    )
    stakeholders: Mapped[List["Stakeholder"]] = relationship(
        "Stakeholder",
        back_populates="deal",
        cascade="all, delete-orphan",
    )


class Milestone(Base):
    """Milestone ORM entity model mapping to the milestones table."""

    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    target_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, native_enum=False),
        nullable=False,
        default=MilestoneStatus.PENDING,
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

    deal: Mapped["Deal"] = relationship(
        "Deal",
        back_populates="milestones",
    )
    critical_tasks: Mapped[List["CriticalTask"]] = relationship(
        "CriticalTask",
        back_populates="milestone",
        cascade="all, delete-orphan",
    )


class CriticalTask(Base):
    """CriticalTask ORM entity model mapping to the critical_tasks table."""

    __tablename__ = "critical_tasks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    milestone_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("milestones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    due_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    is_critical: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    is_overdue: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    escalated_to_supervisor: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    escalated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
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

    milestone: Mapped["Milestone"] = relationship(
        "Milestone",
        back_populates="critical_tasks",
    )


class Stakeholder(Base):
    """Stakeholder ORM entity model mapping to the stakeholders table."""

    __tablename__ = "stakeholders"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    org_role: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    contact_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    parent_stakeholder_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("stakeholders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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

    deal: Mapped["Deal"] = relationship(
        "Deal",
        back_populates="stakeholders",
    )
    parent_stakeholder: Mapped[Optional["Stakeholder"]] = relationship(
        "Stakeholder",
        remote_side=[id],
        back_populates="subordinates",
    )
    subordinates: Mapped[List["Stakeholder"]] = relationship(
        "Stakeholder",
        back_populates="parent_stakeholder",
    )
