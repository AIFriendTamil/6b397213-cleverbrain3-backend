"""Unit tests for metric aggregator service (trend math, caching, domain aggregation, error isolation)."""

from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from src.models.base import Base, create_db_engine
from src.services.dashboard.aggregator import (
    aggregate_dashboard_metrics,
    aggregate_domain_metrics,
    calculate_trend,
    get_kpi_label,
    update_domain_metric_cache,
    update_historical_snapshot,
)


def test_calculate_trend():
    """Test directional trend calculation and percentage math across various inputs."""
    # Standard positive change
    direction, pct = calculate_trend(120.0, 100.0)
    assert direction == "up"
    assert pct == 20.0

    # Standard negative change
    direction, pct = calculate_trend(80.0, 100.0)
    assert direction == "down"
    assert pct == -20.0

    # Zero change
    direction, pct = calculate_trend(100.0, 100.0)
    assert direction == "flat"
    assert pct == 0.0

    # Missing values
    assert calculate_trend(100.0, None) == ("flat", 0.0)
    assert calculate_trend(None, 100.0) == ("flat", 0.0)
    assert calculate_trend(None, None) == ("flat", 0.0)

    # Zero baseline edge cases
    assert calculate_trend(50.0, 0.0) == ("up", 100.0)
    assert calculate_trend(-50.0, 0.0) == ("down", -100.0)
    assert calculate_trend(0.0, 0.0) == ("flat", 0.0)


def test_get_kpi_label():
    """Test KPI display label resolution."""
    assert get_kpi_label("pipeline_value") == "Pipeline Value"
    assert get_kpi_label("conversion_rate") == "Conversion Rate"
    assert get_kpi_label("custom_metric_name") == "Custom Metric Name"


def test_update_domain_metric_cache_and_historical_snapshot():
    """Test inserting and updating metric cache and historical snapshot entries."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        # Create cache entry
        cache_item = update_domain_metric_cache(
            db_session=db_session,
            domain_name="sales",
            metric_key="pipeline_value",
            current_value=100000.0,
            unit_type="currency",
        )
        assert cache_item.domain_name == "sales"
        assert cache_item.metric_key == "pipeline_value"
        assert cache_item.current_value == 100000.0

        # Update existing cache entry
        updated_cache = update_domain_metric_cache(
            db_session=db_session,
            domain_name="sales",
            metric_key="pipeline_value",
            current_value=150000.0,
            unit_type="currency",
        )
        assert updated_cache.id == cache_item.id
        assert updated_cache.current_value == 150000.0

        # Create historical snapshot
        snapshot_item = update_historical_snapshot(
            db_session=db_session,
            domain_name="sales",
            metric_key="pipeline_value",
            baseline_period="prior_month",
            snapshot_value=120000.0,
        )
        assert snapshot_item.domain_name == "sales"
        assert snapshot_item.metric_key == "pipeline_value"
        assert snapshot_item.baseline_period == "prior_month"
        assert snapshot_item.snapshot_value == 120000.0

        # Update historical snapshot
        updated_snapshot = update_historical_snapshot(
            db_session=db_session,
            domain_name="sales",
            metric_key="pipeline_value",
            baseline_period="prior_month",
            snapshot_value=125000.0,
        )
        assert updated_snapshot.id == snapshot_item.id
        assert updated_snapshot.snapshot_value == 125000.0


def test_aggregate_domain_metrics_active():
    """Test aggregating domain metrics with active cache data and trend math."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        update_domain_metric_cache(
            db_session, "sales", "pipeline_value", 1500000.0, "currency"
        )
        update_domain_metric_cache(db_session, "sales", "deals_closed", 45.0, "count")
        update_historical_snapshot(
            db_session, "sales", "pipeline_value", "prior_month", 1200000.0
        )
        update_historical_snapshot(
            db_session, "sales", "deals_closed", "prior_month", 50.0
        )

        domain_data = aggregate_domain_metrics(
            db_session=db_session, domain_name="sales", baseline_period="prior_month"
        )

        assert domain_data["domain_name"] == "sales"
        assert domain_data["display_name"] == "Sales Pipeline"
        assert domain_data["status"] == "active"
        assert domain_data["detail_url"] == "/sales"
        assert len(domain_data["kpis"]) == 2

        kpi_map = {k["key"]: k for k in domain_data["kpis"]}
        pipeline_kpi = kpi_map["pipeline_value"]
        assert pipeline_kpi["value"] == 1500000.0
        assert pipeline_kpi["trend_direction"] == "up"
        assert pipeline_kpi["trend_percentage"] == 25.0

        deals_kpi = kpi_map["deals_closed"]
        assert deals_kpi["value"] == 45.0
        assert deals_kpi["trend_direction"] == "down"
        assert deals_kpi["trend_percentage"] == -10.0


def test_aggregate_domain_metrics_no_data():
    """Test domain aggregation when no cache records exist (empty state)."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        domain_data = aggregate_domain_metrics(
            db_session=db_session,
            domain_name="support",
            baseline_period="prior_month",
        )

        assert domain_data["domain_name"] == "support"
        assert domain_data["display_name"] == "Customer Support"
        assert domain_data["status"] == "no_data"
        assert domain_data["detail_url"] == "/support"
        assert domain_data["kpis"] == []


def test_aggregate_domain_metrics_unconfigured():
    """Test domain aggregation when a domain is disabled via feature flag settings."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        with patch(
            "src.services.dashboard.aggregator.settings.enabled_domains",
            ["sales", "leads"],
        ):
            domain_data = aggregate_domain_metrics(
                db_session=db_session,
                domain_name="finance",
                baseline_period="prior_month",
            )
            assert domain_data["domain_name"] == "finance"
            assert domain_data["status"] == "unconfigured"
            assert domain_data["kpis"] == []


def test_aggregate_domain_metrics_isolated_error():
    """Test domain aggregation error isolation when a query fails."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        with patch.object(
            db_session, "query", side_effect=SQLAlchemyError("DB Connection Lost")
        ):
            domain_data = aggregate_domain_metrics(
                db_session=db_session,
                domain_name="sales",
                baseline_period="prior_month",
            )
            assert domain_data["domain_name"] == "sales"
            assert domain_data["status"] == "error"
            assert domain_data["kpis"] == []


def test_aggregate_dashboard_metrics_with_roles_and_error_isolation():
    """Test aggregate_dashboard_metrics role filtering and multi-domain error isolation."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with DBSession(engine) as db_session:
        # Populate sales and support metrics
        update_domain_metric_cache(
            db_session, "sales", "pipeline_value", 500000.0, "currency"
        )
        update_domain_metric_cache(
            db_session, "support", "open_tickets", 12.0, "count"
        )

        # 1. Role = sales_rep (only sales domain authorized)
        rep_result = aggregate_dashboard_metrics(
            db_session=db_session, roles="sales_rep"
        )
        assert len(rep_result["metrics"]) == 1
        assert rep_result["metrics"][0]["domain_name"] == "sales"
        assert rep_result["metrics"][0]["status"] == "active"

        # 2. Role = admin (all domains authorized)
        admin_result = aggregate_dashboard_metrics(
            db_session=db_session, roles="admin"
        )
        assert len(admin_result["metrics"]) == 4
        domain_names = [m["domain_name"] for m in admin_result["metrics"]]
        assert domain_names == ["sales", "leads", "support", "finance"]

        sales_card = admin_result["metrics"][0]
        leads_card = admin_result["metrics"][1]
        support_card = admin_result["metrics"][2]

        assert sales_card["status"] == "active"
        assert leads_card["status"] == "no_data"
        assert support_card["status"] == "active"

        # 3. Multi-domain error isolation: mock error on leads domain only
        def side_effect_aggregate(db_session, domain_name, baseline_period):
            if domain_name == "leads":
                return {
                    "domain_name": "leads",
                    "display_name": "Lead Conversion",
                    "status": "error",
                    "detail_url": "/leads",
                    "kpis": [],
                }
            return aggregate_domain_metrics(
                db_session=db_session,
                domain_name=domain_name,
                baseline_period=baseline_period,
            )

        with patch(
            "src.services.dashboard.aggregator.aggregate_domain_metrics",
            side_effect=side_effect_aggregate,
        ):
            res = aggregate_dashboard_metrics(db_session=db_session, roles="admin")
            status_map = {m["domain_name"]: m["status"] for m in res["metrics"]}
            assert status_map["sales"] == "active"
            assert status_map["leads"] == "error"
            assert status_map["support"] == "active"
            assert status_map["finance"] == "no_data"
