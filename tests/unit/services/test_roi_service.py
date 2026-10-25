"""Unit tests for ROI calculation formulas, zero-revenue handling, and lead source/campaign analytics."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.campaign import Campaign
from src.models.lead import Lead, LeadSource
from src.services.analytics.roi_service import (
    CampaignROIMetrics,
    SourceROIMetrics,
    calculate_roi_percentage,
    get_campaign_roi_metrics,
    get_lead_source_roi_metrics,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with initialized tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_calculate_roi_percentage_positive():
    """Verify standard positive ROI calculations with float, Decimal, and integer inputs."""
    # (5000 - 1000) / 1000 * 100 = 400.0%
    assert calculate_roi_percentage(5000, 1000) == 400.0
    assert calculate_roi_percentage(Decimal("2500.00"), Decimal("1000.00")) == 150.0
    assert calculate_roi_percentage(150.0, 100.0) == 50.0


def test_calculate_roi_percentage_zero_revenue():
    """Verify zero or negative revenue produces 0.0% ROI without exception."""
    assert calculate_roi_percentage(0, 1000) == 0.0
    assert calculate_roi_percentage(Decimal("0.00"), Decimal("500.00")) == 0.0
    assert calculate_roi_percentage(-100, 500) == 0.0


def test_calculate_roi_percentage_zero_cost():
    """Verify zero or negative cost/spend produces 0.0% ROI without division by zero."""
    assert calculate_roi_percentage(5000, 0) == 0.0
    assert calculate_roi_percentage(Decimal("1000.00"), Decimal("0.00")) == 0.0
    assert calculate_roi_percentage(500, -50) == 0.0


def test_calculate_roi_percentage_both_zero():
    """Verify both zero revenue and zero cost produce 0.0% ROI."""
    assert calculate_roi_percentage(0, 0) == 0.0
    assert calculate_roi_percentage(None, None) == 0.0


def test_get_lead_source_roi_metrics_empty_db(test_session: Session):
    """Verify empty database returns empty lead source metrics list."""
    results = get_lead_source_roi_metrics(test_session)
    assert results == []


def test_get_lead_source_roi_metrics_calculation(test_session: Session):
    """Verify lead source metrics, quality averages, revenue aggregation, and ROI computation."""
    source1 = LeadSource(
        source_name="Paid Search",
        cost_amount=Decimal("1000.00"),
    )
    source2 = LeadSource(
        source_name="Organic Social",
        cost_amount=Decimal("500.00"),
    )
    test_session.add_all([source1, source2])
    test_session.commit()

    # Add leads to Paid Search
    lead1 = Lead(
        source_id=source1.source_id,
        quality_score=80,
        deal_value=Decimal("3000.00"),
    )
    lead2 = Lead(
        source_id=source1.source_id,
        quality_score=90,
        deal_value=Decimal("2000.00"),
    )
    test_session.add_all([lead1, lead2])
    test_session.commit()

    results = get_lead_source_roi_metrics(test_session)
    assert len(results) == 2

    # Results ordered by source_name ("Organic Social", "Paid Search")
    organic_res = results[0]
    assert organic_res.source_name == "Organic Social"
    assert organic_res.total_leads == 0
    assert organic_res.average_quality_score == 0.0
    assert organic_res.total_spend == 500.0
    assert organic_res.attributed_revenue == 0.0
    assert organic_res.calculated_roi_percentage == 0.0

    paid_res = results[1]
    assert paid_res.source_name == "Paid Search"
    assert paid_res.total_leads == 2
    assert paid_res.average_quality_score == 85.0
    assert paid_res.total_spend == 1000.0
    assert paid_res.attributed_revenue == 5000.0
    # ROI: (5000 - 1000) / 1000 * 100 = 400.0%
    assert paid_res.calculated_roi_percentage == 400.0

    # Verify to_dict serialization
    paid_dict = paid_res.to_dict()
    assert paid_dict["source_id"] == source1.source_id
    assert paid_dict["calculated_roi_percentage"] == 400.0


def test_get_lead_source_roi_metrics_filtering(test_session: Session):
    """Verify filtering lead source metrics by source_id and timestamp range."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=30)

    source = LeadSource(source_name="Events", cost_amount=Decimal("2000.00"))
    test_session.add(source)
    test_session.commit()

    lead_recent = Lead(
        source_id=source.source_id,
        quality_score=70,
        deal_value=Decimal("4000.00"),
        created_timestamp=now,
    )
    lead_old = Lead(
        source_id=source.source_id,
        quality_score=50,
        deal_value=Decimal("1000.00"),
        created_timestamp=old_time,
    )
    test_session.add_all([lead_recent, lead_old])
    test_session.commit()

    # Filter by source_id
    by_id = get_lead_source_roi_metrics(test_session, source_id=source.source_id)
    assert len(by_id) == 1
    assert by_id[0].total_leads == 2
    assert by_id[0].attributed_revenue == 5000.0

    # Filter by start_timestamp (recent only)
    start_filter = now - timedelta(days=5)
    recent_only = get_lead_source_roi_metrics(test_session, start_timestamp=start_filter)
    assert len(recent_only) == 1
    assert recent_only[0].total_leads == 1
    assert recent_only[0].attributed_revenue == 4000.0
    assert recent_only[0].average_quality_score == 70.0


def test_get_campaign_roi_metrics(test_session: Session):
    """Verify campaign performance matrix, conversion rate, and ROI calculation."""
    campaign = Campaign(
        campaign_name="Q1 Growth Launch",
        budget_amount=Decimal("10000.00"),
        spent_amount=Decimal("8000.00"),
    )
    source = LeadSource(source_name="Campaign Source", cost_amount=Decimal("0.00"))
    test_session.add_all([campaign, source])
    test_session.commit()

    lead1 = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=90,
        status_stage="CLOSED_WON",
        deal_value=Decimal("16000.00"),
    )
    lead2 = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=70,
        status_stage="QUALIFIED",
        deal_value=Decimal("0.00"),
    )
    test_session.add_all([lead1, lead2])
    test_session.commit()

    metrics = get_campaign_roi_metrics(test_session, campaign_ids=[campaign.campaign_id])
    assert len(metrics) == 1

    cmp_res = metrics[0]
    assert cmp_res.campaign_name == "Q1 Growth Launch"
    assert cmp_res.leads_generated == 2
    assert cmp_res.average_quality_score == 80.0
    assert cmp_res.overall_conversion_rate == 50.0  # 1 of 2 converted
    assert cmp_res.total_spend == 8000.0
    assert cmp_res.attributed_revenue == 16000.0
    # ROI: (16000 - 8000) / 8000 * 100 = 100.0%
    assert cmp_res.total_roi_percentage == 100.0

    cmp_dict = cmp_res.to_dict()
    assert cmp_dict["campaign_id"] == campaign.campaign_id
    assert cmp_dict["total_roi_percentage"] == 100.0
