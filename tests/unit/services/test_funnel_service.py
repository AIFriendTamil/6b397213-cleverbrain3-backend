"""Unit tests for conversion rates, bottleneck detection, and funnel stage drill-down service."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.funnel import FunnelStage
from src.models.lead import Lead, LeadSource
from src.services.analytics.funnel_service import (
    FunnelStageMetrics,
    StageLeadDetail,
    StageLeadDrillDownResponse,
    calculate_lead_time_in_stage,
    calculate_stage_conversion_rate,
    detect_stage_bottleneck,
    get_funnel_stage_metrics,
    get_stage_lead_drilldown,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with initialized tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_calculate_stage_conversion_rate_first_stage():
    """Verify conversion rate for the initial stage where previous_count is None."""
    assert calculate_stage_conversion_rate(10, None) == 100.0
    assert calculate_stage_conversion_rate(1, None) == 100.0
    assert calculate_stage_conversion_rate(0, None) == 0.0


def test_calculate_stage_conversion_rate_subsequent_stages():
    """Verify conversion rates for subsequent stages based on preceding stage count."""
    # 8 / 10 = 80.0%
    assert calculate_stage_conversion_rate(8, 10) == 80.0
    # 1 / 4 = 25.0%
    assert calculate_stage_conversion_rate(1, 4) == 25.0
    # 0 / 10 = 0.0%
    assert calculate_stage_conversion_rate(0, 10) == 0.0
    # Division by zero protection when preceding count is 0
    assert calculate_stage_conversion_rate(5, 0) == 0.0
    assert calculate_stage_conversion_rate(0, 0) == 0.0


def test_detect_stage_bottleneck():
    """Verify bottleneck detection when conversion rate falls below target thresholds."""
    # Rate below target -> Bottleneck
    assert detect_stage_bottleneck(15.0, 20.0) is True
    # Rate equal to or above target -> Not bottleneck
    assert detect_stage_bottleneck(20.0, 20.0) is False
    assert detect_stage_bottleneck(25.0, 20.0) is False

    # Fallback to default threshold when stage target is 0.0
    assert detect_stage_bottleneck(10.0, 0.0, default_threshold=20.0) is True
    assert detect_stage_bottleneck(25.0, 0.0, default_threshold=20.0) is False


def test_calculate_lead_time_in_stage():
    """Verify time in stage calculation in fractional days."""
    now = datetime.now(timezone.utc)
    two_days_ago = now - timedelta(days=2)
    half_day_ago = now - timedelta(hours=12)

    assert calculate_lead_time_in_stage(two_days_ago, now) == 2.0
    assert calculate_lead_time_in_stage(half_day_ago, now) == 0.5
    # Future timestamp returns 0.0
    future = now + timedelta(days=1)
    assert calculate_lead_time_in_stage(future, now) == 0.0


def test_get_funnel_stage_metrics_empty_db(test_session: Session):
    """Verify empty database returns empty funnel stage metrics list."""
    results = get_funnel_stage_metrics(test_session)
    assert results == []


def test_get_funnel_stage_metrics_computation(test_session: Session):
    """Verify funnel stage metrics, sequential order, conversion rates, and bottleneck flags."""
    # Create 3 funnel stages in non-sequential insertion order
    stage3 = FunnelStage(stage_name="Proposal Sent", stage_order=3, target_conversion_rate=Decimal("40.00"))
    stage1 = FunnelStage(stage_name="Lead Captured", stage_order=1, target_conversion_rate=Decimal("50.00"))
    stage2 = FunnelStage(stage_name="Qualified", stage_order=2, target_conversion_rate=Decimal("50.00"))

    test_session.add_all([stage3, stage1, stage2])
    test_session.commit()

    source = LeadSource(source_name="Inbound Web", cost_amount=Decimal("1000.00"))
    test_session.add(source)
    test_session.commit()

    # Create 10 leads in stage 1, 4 in stage 2, 1 in stage 3
    leads = []
    for _ in range(10):
        leads.append(Lead(source_id=source.source_id, status_stage="Lead Captured"))
    for _ in range(4):
        leads.append(Lead(source_id=source.source_id, status_stage="Qualified"))
    for _ in range(1):
        leads.append(Lead(source_id=source.source_id, status_stage="Proposal Sent"))

    test_session.add_all(leads)
    test_session.commit()

    metrics = get_funnel_stage_metrics(test_session)
    assert len(metrics) == 3

    # Stage 1: Lead Captured (10 leads, 100.0% conv rate, target 50.0% -> not bottleneck)
    s1 = metrics[0]
    assert s1.stage_name == "Lead Captured"
    assert s1.stage_order == 1
    assert s1.lead_count == 10
    assert s1.conversion_rate_percentage == 100.0
    assert s1.target_conversion_rate == 50.0
    assert s1.is_bottleneck is False

    # Stage 2: Qualified (4 leads vs 10 prev -> 40.0% conv rate, target 50.0% -> bottleneck!)
    s2 = metrics[1]
    assert s2.stage_name == "Qualified"
    assert s2.stage_order == 2
    assert s2.lead_count == 4
    assert s2.conversion_rate_percentage == 40.0
    assert s2.target_conversion_rate == 50.0
    assert s2.is_bottleneck is True

    # Stage 3: Proposal Sent (1 lead vs 4 prev -> 25.0% conv rate, target 40.0% -> bottleneck!)
    s3 = metrics[2]
    assert s3.stage_name == "Proposal Sent"
    assert s3.stage_order == 3
    assert s3.lead_count == 1
    assert s3.conversion_rate_percentage == 25.0
    assert s3.target_conversion_rate == 40.0
    assert s3.is_bottleneck is True


def test_get_funnel_stage_metrics_with_filters(test_session: Session):
    """Verify filtering by source_id and creation timestamps on funnel stage metrics."""
    stage1 = FunnelStage(stage_name="Initial Contact", stage_order=1, target_conversion_rate=Decimal("20.00"))
    test_session.add(stage1)

    source_a = LeadSource(source_name="Source A", cost_amount=Decimal("100.00"))
    source_b = LeadSource(source_name="Source B", cost_amount=Decimal("200.00"))
    test_session.add_all([source_a, source_b])
    test_session.commit()

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=10)

    lead_a = Lead(
        source_id=source_a.source_id,
        status_stage="Initial Contact",
        created_timestamp=now,
    )
    lead_b = Lead(
        source_id=source_b.source_id,
        status_stage="Initial Contact",
        created_timestamp=now,
    )
    lead_old = Lead(
        source_id=source_a.source_id,
        status_stage="Initial Contact",
        created_timestamp=old_time,
    )
    test_session.add_all([lead_a, lead_b, lead_old])
    test_session.commit()

    # Filter by source_a only -> expects 2 leads
    metrics_a = get_funnel_stage_metrics(test_session, source_id=source_a.source_id)
    assert metrics_a[0].lead_count == 2

    # Filter by start_timestamp (recent only) -> expects 1 lead for source_a
    recent_start = now - timedelta(days=1)
    metrics_recent = get_funnel_stage_metrics(
        test_session,
        source_id=source_a.source_id,
        start_timestamp=recent_start,
    )
    assert metrics_recent[0].lead_count == 1


def test_get_stage_lead_drilldown_success(test_session: Session):
    """Verify detailed lead drill-down for a valid funnel stage."""
    stage = FunnelStage(stage_name="QUALIFIED", stage_order=1)
    source = LeadSource(source_name="Direct", cost_amount=Decimal("0.00"))
    test_session.add_all([stage, source])
    test_session.commit()

    ref_time = datetime.now(timezone.utc)
    created_time = ref_time - timedelta(days=3)

    lead = Lead(
        source_id=source.source_id,
        quality_score=95,
        status_stage="QUALIFIED",
        state_code="NY",
        postal_code="10001",
        region_name="East",
        deal_value=Decimal("15000.00"),
        created_timestamp=created_time,
    )
    test_session.add(lead)
    test_session.commit()

    drilldown = get_stage_lead_drilldown(
        test_session,
        stage_id="QUALIFIED",
        reference_time=ref_time,
    )

    assert drilldown is not None
    assert drilldown.stage_id == stage.stage_id
    assert drilldown.stage_name == "QUALIFIED"
    assert drilldown.total_leads == 1
    assert drilldown.page == 1
    assert drilldown.limit == 20
    assert len(drilldown.leads) == 1

    lead_detail = drilldown.leads[0]
    assert lead_detail.lead_id == lead.lead_id
    assert lead_detail.quality_score == 95
    assert lead_detail.region_name == "East"
    assert lead_detail.deal_value == 15000.0
    assert lead_detail.time_in_stage_days == 3.0

    d = drilldown.to_dict()
    assert d["total_leads"] == 1
    assert d["leads"][0]["lead_id"] == lead.lead_id
    assert isinstance(d["leads"][0]["created_timestamp"], str)


def test_get_stage_lead_drilldown_non_existent_stage(test_session: Session):
    """Verify drill-down returns None for non-existent stage identifiers."""
    drilldown = get_stage_lead_drilldown(test_session, stage_id="non-existent-uuid")
    assert drilldown is None


def test_get_stage_lead_drilldown_pagination(test_session: Session):
    """Verify pagination controls (page and limit) in stage lead drill-down."""
    stage = FunnelStage(stage_name="Inquiry", stage_order=1)
    source = LeadSource(source_name="Web", cost_amount=Decimal("0.00"))
    test_session.add_all([stage, source])
    test_session.commit()

    # Create 55 leads
    leads = [
        Lead(source_id=source.source_id, status_stage="Inquiry", quality_score=i)
        for i in range(55)
    ]
    test_session.add_all(leads)
    test_session.commit()

    # Page 1, Limit 20 -> expect 20 leads
    res1 = get_stage_lead_drilldown(test_session, stage_id=stage.stage_id, page=1, limit=20)
    assert res1 is not None
    assert res1.total_leads == 55
    assert len(res1.leads) == 20

    # Page 3, Limit 20 -> expect 15 leads (41..55)
    res3 = get_stage_lead_drilldown(test_session, stage_id=stage.stage_id, page=3, limit=20)
    assert res3 is not None
    assert res3.total_leads == 55
    assert len(res3.leads) == 15
