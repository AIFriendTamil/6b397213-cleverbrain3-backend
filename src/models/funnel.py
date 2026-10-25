"""SQLAlchemy ORM model for FunnelStage entity."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class FunnelStage(Base):
    """FunnelStage ORM entity model mapping to the funnel_stages table."""

    __tablename__ = "funnel_stages"

    stage_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    stage_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    stage_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        index=True,
    )
    target_conversion_rate: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def id(self) -> str:
        """Alias property for stage_id for standard entity primary key access."""
        return self.stage_id

    def __repr__(self) -> str:
        """String representation of FunnelStage model instance."""
        return (
            f"<FunnelStage(stage_id='{self.stage_id}', "
            f"stage_name='{self.stage_name}', "
            f"stage_order={self.stage_order}, "
            f"target_conversion_rate={self.target_conversion_rate})>"
        )
