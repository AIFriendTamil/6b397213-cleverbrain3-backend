"""Unit tests for Dashboard ORM models (DashboardMetricCache and DashboardHistoricalSnapshot)."""

from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from src.models.base import Base, create_db_engine
from src.models.dashboard import DashboardHistoricalSnapshot, DashboardMetricCache


def test_dashboard_metric_cache_fields_and_persistence():
    """Verify DashboardMetricCache creation, defaults, fields, and DB persistence."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with DBSession(test_engine) as db_session:
        cache_entry = DashboardMetricCache(
            domain_name="sales",
            metric_key="pipeline_value",
            current_value=1250000.50,
            unit_type="currency",
        )
        db_session.add(cache_entry)
        db_session.commit()

        # Retrieve persisted record
        retrieved = (
            db_session.query(DashboardMetricCache)
            .filter_by(domain_name="sales", metric_key="pipeline_value")
            .first()
        )
        assert retrieved is not None
        assert isinstance(retrieved.id, int)
        assert retrieved.id > 0
        assert retrieved.domain_name == "sales"
        assert retrieved.metric_key == "pipeline_value"
        assert retrieved.current_value == 1250000.50
        assert retrieved.unit_type == "currency"
        assert isinstance(retrieved.updated_at, datetime)


def test_dashboard_metric_cache_unique_constraint():
    """Verify unique constraint on (domain_name, metric_key) raises IntegrityError on duplicate."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with DBSession(test_engine) as db_session:
        entry1 = DashboardMetricCache(
            domain_name="leads",
            metric_key="conversion_rate",
            current_value=18.5,
            unit_type="percentage",
        )
        db_session.add(entry1)
        db_session.commit()

        entry2 = DashboardMetricCache(
            domain_name="leads",
            metric_key="conversion_rate",
            current_value=20.0,
            unit_type="percentage",
        )
        db_session.add(entry2)
        with pytest.raises(IntegrityError):
            db_session.commit()


def test_dashboard_historical_snapshot_fields_and_persistence():
    """Verify DashboardHistoricalSnapshot creation, defaults, fields, and DB persistence."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with DBSession(test_engine) as db_session:
        snapshot = DashboardHistoricalSnapshot(
            domain_name="finance",
            metric_key="account_balance",
            baseline_period="prior_month",
            snapshot_value=450000.0,
        )
        db_session.add(snapshot)
        db_session.commit()

        # Retrieve persisted record
        retrieved = (
            db_session.query(DashboardHistoricalSnapshot)
            .filter_by(domain_name="finance", metric_key="account_balance")
            .first()
        )
        assert retrieved is not None
        assert isinstance(retrieved.id, int)
        assert retrieved.id > 0
        assert retrieved.domain_name == "finance"
        assert retrieved.metric_key == "account_balance"
        assert retrieved.baseline_period == "prior_month"
        assert retrieved.snapshot_value == 450000.0
        assert isinstance(retrieved.captured_at, datetime)


def test_dashboard_models_tablename_and_column_names():
    """Verify canonical table names and confirm no prohibited 'date' column exists."""
    assert DashboardMetricCache.__tablename__ == "dashboard_metric_cache"
    assert DashboardHistoricalSnapshot.__tablename__ == "dashboard_historical_snapshots"

    for model_cls in [DashboardMetricCache, DashboardHistoricalSnapshot]:
        column_names = [col.name for col in model_cls.__table__.columns]
        assert "date" not in column_names, f"'date' column found in {model_cls.__name__}"
