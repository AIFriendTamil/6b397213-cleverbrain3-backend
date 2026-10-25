"""Unit tests for periodic Celery background worker job in dashboard_refresh.py."""

from unittest.mock import patch
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from src.models.base import Base, create_db_engine
from src.models.dashboard import DashboardHistoricalSnapshot, DashboardMetricCache
from src.workers.celery_app import celery_app
from src.workers.dashboard_refresh import (
    DEFAULT_DOMAIN_METRICS,
    recalculate_dashboard_metrics,
    refresh_domain_metrics,
)


def test_refresh_domain_metrics_defaults():
    """Test refresh_domain_metrics with default domain metric specs."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        updated_count = refresh_domain_metrics(
            db_session=db_session,
            domain_name="sales",
        )
        assert updated_count == 3

        # Verify cached records
        records = (
            db_session.query(DashboardMetricCache)
            .filter_by(domain_name="sales")
            .all()
        )
        assert len(records) == 3
        metric_keys = {r.metric_key for r in records}
        assert metric_keys == {"pipeline_value", "deals_closed", "win_rate"}

        # Verify historical snapshot records
        snapshots = (
            db_session.query(DashboardHistoricalSnapshot)
            .filter_by(domain_name="sales")
            .all()
        )
        assert len(snapshots) == 6  # 3 metrics * 2 baselines each


def test_refresh_domain_metrics_custom_overrides():
    """Test refresh_domain_metrics with custom metric items."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    custom_items = [
        {
            "metric_key": "custom_revenue",
            "current_value": 500000.0,
            "unit_type": "currency",
            "baselines": {"prior_month": 450000.0},
        }
    ]

    with DBSession(engine) as db_session:
        updated_count = refresh_domain_metrics(
            db_session=db_session,
            domain_name="finance",
            metric_items=custom_items,
        )
        assert updated_count == 1

        record = (
            db_session.query(DashboardMetricCache)
            .filter_by(domain_name="finance", metric_key="custom_revenue")
            .first()
        )
        assert record is not None
        assert record.current_value == 500000.0

        snapshot = (
            db_session.query(DashboardHistoricalSnapshot)
            .filter_by(
                domain_name="finance",
                metric_key="custom_revenue",
                baseline_period="prior_month",
            )
            .first()
        )
        assert snapshot is not None
        assert snapshot.snapshot_value == 450000.0


def test_recalculate_dashboard_metrics_all_domains():
    """Test recalculate_dashboard_metrics Celery task across enabled domains."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        result = recalculate_dashboard_metrics(db_session=db_session)

        assert result["status"] == "completed"
        assert set(result["processed_domains"]) == {"sales", "leads", "support", "finance"}
        assert result["metrics_updated"] == 12  # 3 metrics * 4 domains
        assert "timestamp" in result

        # Verify database contents
        cached_count = db_session.query(DashboardMetricCache).count()
        assert cached_count == 12


def test_recalculate_dashboard_metrics_subset_domains_and_overrides():
    """Test recalculate_dashboard_metrics with explicit domains list and overrides."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    overrides = {
        "sales": [
            {
                "metric_key": "pipeline_value",
                "current_value": 2000000.0,
                "unit_type": "currency",
                "baselines": {"prior_month": 1500000.0},
            }
        ]
    }

    with DBSession(engine) as db_session:
        result = recalculate_dashboard_metrics(
            domains=["sales"],
            metric_overrides=overrides,
            db_session=db_session,
        )

        assert result["status"] == "completed"
        assert result["processed_domains"] == ["sales"]
        assert result["metrics_updated"] == 1

        record = (
            db_session.query(DashboardMetricCache)
            .filter_by(domain_name="sales", metric_key="pipeline_value")
            .first()
        )
        assert record is not None
        assert record.current_value == 2000000.0


def test_recalculate_dashboard_metrics_error_isolation():
    """Test domain error isolation during recalculate_dashboard_metrics task execution."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    def mock_refresh(db_session, domain_name, metric_items=None):
        if domain_name == "leads":
            raise SQLAlchemyError("Database write error on leads domain")
        return refresh_domain_metrics(db_session, domain_name, metric_items)

    with DBSession(engine) as db_session:
        with patch(
            "src.workers.dashboard_refresh.refresh_domain_metrics",
            side_effect=mock_refresh,
        ):
            result = recalculate_dashboard_metrics(
                domains=["sales", "leads", "support"],
                db_session=db_session,
            )

            assert result["status"] == "completed_with_errors"
            assert "sales" in result["processed_domains"]
            assert "support" in result["processed_domains"]
            assert "leads" not in result["processed_domains"]
            assert "leads" in result["errors"]
            assert "Database write error" in result["errors"]["leads"]


def test_celery_beat_schedule_registration():
    """Test that dashboard_refresh task is properly registered in Celery beat schedule."""
    conf = celery_app.conf
    assert "src.workers.dashboard_refresh" in conf.imports
    assert "recalculate-dashboard-metrics-periodic" in conf.beat_schedule
    schedule_entry = conf.beat_schedule["recalculate-dashboard-metrics-periodic"]
    assert schedule_entry["task"] == "src.workers.dashboard_refresh.recalculate_dashboard_metrics"
    assert schedule_entry["schedule"] == 60.0
