"""Unit tests for side-by-side campaign comparison logic and matrix generation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.campaign import Campaign
from src.models.lead import Lead, LeadSource
from src.services.campaigns.comparison_service import (
    CampaignComparisonItem,
    CampaignComparisonMatrix,
    CampaignComparisonSummary,
    calculate_campaign_item_metrics,
    compare_campaigns,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with initialized tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_calculate_campaign_item_metrics_no_leads():
    """Verify metrics calculation for a campaign with zero generated leads."""
    campaign = Campaign(
        campaign_name="Test Campaign No Leads",
        budget_amount=Decimal("1000.00"),
        spent_amount=Decimal("500.00"),
    )
    item = calculate_campaign_item_metrics(campaign, [])

    assert item.campaign_name == "Test Campaign No Leads"
    assert item.budget_amount == 1000.0
    assert item.spent_amount == 500.0
    assert item.leads_generated == 0
    assert item.average_quality_score == 0.0
    assert item.converted_leads == 0
    assert item.overall_conversion_rate == 0.0
    assert item.attributed_revenue == 0.0
    assert item.total_roi_percentage == 0.0
    assert item.cost_per_lead == 0.0
    assert item.cost_per_conversion == 0.0


def test_calculate_campaign_item_metrics_with_leads():
    """Verify metrics calculation for a campaign with active and converted leads."""
    campaign = Campaign(
        campaign_name="Active Campaign",
        budget_amount=Decimal("2000.00"),
        spent_amount=Decimal("1000.00"),
    )
    lead1 = Lead(
        quality_score=80,
        deal_value=Decimal("3000.00"),
        status_stage="CLOSED_WON",
    )
    lead2 = Lead(
        quality_score=60,
        deal_value=Decimal("0.00"),
        status_stage="QUALIFIED",
    )
    item = calculate_campaign_item_metrics(campaign, [lead1, lead2])

    assert item.campaign_name == "Active Campaign"
    assert item.leads_generated == 2
    assert item.average_quality_score == 70.0
    assert item.converted_leads == 1
    assert item.overall_conversion_rate == 50.0
    assert item.attributed_revenue == 3000.0
    # ROI: ((3000 - 1000) / 1000) * 100 = 200.0%
    assert item.total_roi_percentage == 200.0
    assert item.cost_per_lead == 500.0
    assert item.cost_per_conversion == 1000.0


def test_compare_campaigns_empty_db(test_session: Session):
    """Verify empty database comparison returns empty matrix and zeroed summary."""
    matrix = compare_campaigns(test_session)
    assert matrix.campaigns == []
    assert matrix.summary.total_campaigns_compared == 0
    assert matrix.summary.best_roi_campaign_id is None
    assert matrix.summary.total_spend_across_campaigns == 0.0


def test_compare_campaigns_empty_campaign_ids_list(test_session: Session):
    """Verify passing an empty list of campaign_ids returns an empty comparison matrix."""
    matrix = compare_campaigns(test_session, campaign_ids=[])
    assert matrix.campaigns == []
    assert matrix.summary.total_campaigns_compared == 0


def test_compare_campaigns_side_by_side(test_session: Session):
    """Verify side-by-side comparison matrix across multiple active campaigns."""
    source = LeadSource(source_name="Generic Source")
    test_session.add(source)
    test_session.commit()

    campaign_a = Campaign(
        campaign_name="Alpha Launch",
        budget_amount=Decimal("1000.00"),
        spent_amount=Decimal("1000.00"),
    )
    campaign_b = Campaign(
        campaign_name="Beta Growth",
        budget_amount=Decimal("2000.00"),
        spent_amount=Decimal("2000.00"),
    )
    test_session.add_all([campaign_a, campaign_b])
    test_session.commit()

    # Campaign A: 1 lead, $5000 revenue
    lead_a = Lead(
        source_id=source.source_id,
        campaign_id=campaign_a.campaign_id,
        quality_score=90,
        deal_value=Decimal("5000.00"),
        status_stage="CLOSED_WON",
    )

    # Campaign B: 2 leads, $2000 revenue total (1 converted)
    lead_b1 = Lead(
        source_id=source.source_id,
        campaign_id=campaign_b.campaign_id,
        quality_score=70,
        deal_value=Decimal("2000.00"),
        status_stage="CONVERTED",
    )
    lead_b2 = Lead(
        source_id=source.source_id,
        campaign_id=campaign_b.campaign_id,
        quality_score=50,
        deal_value=Decimal("0.00"),
        status_stage="NEW",
    )
    test_session.add_all([lead_a, lead_b1, lead_b2])
    test_session.commit()

    matrix = compare_campaigns(
        test_session,
        campaign_ids=[campaign_a.campaign_id, campaign_b.campaign_id],
    )

    assert len(matrix.campaigns) == 2
    item_a = next(c for c in matrix.campaigns if c.campaign_id == campaign_a.campaign_id)
    item_b = next(c for c in matrix.campaigns if c.campaign_id == campaign_b.campaign_id)

    # Campaign A: Spend $1000, Revenue $5000 -> ROI 400.0%, Conv 100.0%
    assert item_a.total_roi_percentage == 400.0
    assert item_a.overall_conversion_rate == 100.0

    # Campaign B: Spend $2000, Revenue $2000 -> ROI 0.0%, Conv 50.0%
    assert item_b.total_roi_percentage == 0.0
    assert item_b.overall_conversion_rate == 50.0

    summary = matrix.summary
    assert summary.total_campaigns_compared == 2
    assert summary.best_roi_campaign_id == campaign_a.campaign_id
    assert summary.best_roi_campaign_name == "Alpha Launch"
    assert summary.best_conversion_campaign_id == campaign_a.campaign_id
    assert summary.highest_lead_volume_campaign_id == campaign_b.campaign_id
    assert summary.total_spend_across_campaigns == 3000.0
    assert summary.total_revenue_across_campaigns == 7000.0
    assert summary.total_leads_across_campaigns == 3


def test_compare_campaigns_date_filtering(test_session: Session):
    """Verify lead filtering by date range during campaign comparison."""
    source = LeadSource(source_name="Date Test Source")
    campaign = Campaign(
        campaign_name="Temporal Campaign",
        budget_amount=Decimal("1000.00"),
        spent_amount=Decimal("500.00"),
    )
    test_session.add_all([source, campaign])
    test_session.commit()

    now = datetime.now(timezone.utc)
    old_lead = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=50,
        deal_value=Decimal("1000.00"),
        created_timestamp=now - timedelta(days=10),
    )
    new_lead = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=90,
        deal_value=Decimal("4000.00"),
        created_timestamp=now - timedelta(days=1),
    )
    test_session.add_all([old_lead, new_lead])
    test_session.commit()

    # Filter for only recent leads (last 5 days)
    start_time = now - timedelta(days=5)
    matrix = compare_campaigns(
        test_session,
        campaign_ids=[campaign.campaign_id],
        start_timestamp=start_time,
    )

    assert len(matrix.campaigns) == 1
    item = matrix.campaigns[0]
    assert item.leads_generated == 1
    assert item.attributed_revenue == 4000.0


def test_matrix_to_dict_serialization():
    """Verify serialization of comparison matrix, items, and summary to dictionary format."""
    item = CampaignComparisonItem(
        campaign_id="c1",
        campaign_name="Test Campaign",
        budget_amount=1000.0,
        spent_amount=500.0,
        leads_generated=10,
        average_quality_score=85.0,
        converted_leads=2,
        overall_conversion_rate=20.0,
        attributed_revenue=2000.0,
        total_roi_percentage=300.0,
        cost_per_lead=50.0,
        cost_per_conversion=250.0,
        start_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    summary = CampaignComparisonSummary(
        total_campaigns_compared=1,
        best_roi_campaign_id="c1",
        best_roi_campaign_name="Test Campaign",
        best_conversion_campaign_id="c1",
        best_conversion_campaign_name="Test Campaign",
        highest_lead_volume_campaign_id="c1",
        highest_lead_volume_campaign_name="Test Campaign",
        total_spend_across_campaigns=500.0,
        total_revenue_across_campaigns=2000.0,
        total_leads_across_campaigns=10,
        average_roi_percentage=300.0,
    )
    matrix = CampaignComparisonMatrix(campaigns=[item], summary=summary)
    dict_out = matrix.to_dict()

    assert "campaigns" in dict_out
    assert "summary" in dict_out
    assert dict_out["campaigns"][0]["campaign_id"] == "c1"
    assert dict_out["campaigns"][0]["start_timestamp"] == "2026-01-01T00:00:00+00:00"
    assert dict_out["summary"]["total_campaigns_compared"] == 1
