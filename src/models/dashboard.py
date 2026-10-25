"""Dashboard metric cache and historical snapshot ORM entity models."""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class DashboardMetricCache(Base):
    """ORM entity model mapping to the dashboard_metric_cache table."""

    __tablename__ = "dashboard_metric_cache"
    __table_args__ = (
        UniqueConstraint(
            "domain_name",
            "metric_key",
            name="uq_dashboard_metric_cache_domain_metric",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    domain_name: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    current_value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    unit_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DashboardHistoricalSnapshot(Base):
    """ORM entity model mapping to the dashboard_historical_snapshots table."""

    __tablename__ = "dashboard_historical_snapshots"
    __table_args__ = (
        Index(
            "idx_historical_snapshots_domain_metric_period",
            "domain_name",
            "metric_key",
            "baseline_period",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    domain_name: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    baseline_period: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    snapshot_value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
