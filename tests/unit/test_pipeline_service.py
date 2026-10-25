"""Unit tests for pipeline funnel metrics calculation and win probability aggregation in pipeline_service.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.deal import Deal, DealStage
from src.services.deals.pipeline_service import (
    PipelineFunnelSummary,
    StageFunnelMetrics,
    aggregate_win_probabilities,
    calculate_conversion_rate,
    calculate_deal_age_days,
    calculate_stage_metrics,
    filter_deals,
    filter_deals_query,
    get_pipeline_funnel_metrics,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_calculate_deal_age_days():
    """Verify deal age calculation in fractional days relative to reference timestamp."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    created = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)

    age_days = calculate_deal_age_days(created, reference_time=ref_time)
    assert age_days == 2.0

    # Future created_at should return 0.0
    future_created = datetime(2026, 1, 12, 12, 0, 0, tzinfo=timezone.utc)
    assert calculate_deal_age_days(future_created, reference_time=ref_time) == 0.0


def test_aggregate_win_probabilities_empty():
    """Verify win probability aggregation for an empty list of deals."""
    res = aggregate_win_probabilities([])
    assert res["average_win_probability"] == 0.0
    assert res["min_win_probability"] == 0.0
    assert res["max_win_probability"] == 0.0
    assert res["total_weighted_value"] == 0.0


def test_aggregate_win_probabilities():
    """Verify win probability aggregation across multiple deal entities."""
    d1 = Deal(
        title="Deal 1",
        monetary_value=Decimal("10000.00"),
        win_probability=0.20,
        stage_id="stg_1",
    )
    d2 = Deal(
        title="Deal 2",
        monetary_value=Decimal("20000.00"),
        win_probability=0.80,
        stage_id="stg_1",
    )

    res = aggregate_win_probabilities([d1, d2])
    assert res["average_win_probability"] == 0.50
    assert res["min_win_probability"] == 0.20
    assert res["max_win_probability"] == 0.80
    # Weighted value: (10000 * 0.20) + (20000 * 0.80) = 2000 + 16000 = 18000
    assert res["total_weighted_value"] == 18000.00


def test_calculate_conversion_rate():
    """Verify stage-to-stage conversion rate calculations."""
    assert calculate_conversion_rate(5, None) == 100.0
    assert calculate_conversion_rate(0, None) == 0.0
    assert calculate_conversion_rate(5, 10) == 50.0
    assert calculate_conversion_rate(3, 10) == 30.0
    assert calculate_conversion_rate(5, 0) == 0.0


def test_calculate_stage_metrics_empty_stage():
    """Verify stage metrics for a stage with zero assigned deals."""
    stage = DealStage(name="Discovery", display_order=1)
    stage.id = "stg_disc"

    metrics = calculate_stage_metrics(stage, [], previous_stage_deal_count=10)
    assert metrics.stage_id == "stg_disc"
    assert metrics.stage_name == "Discovery"
    assert metrics.deal_count == 0
    assert metrics.total_monetary_value == 0.0
    assert metrics.conversion_rate_from_previous == 0.0


def test_get_pipeline_funnel_metrics_with_data(test_session: Session):
    """Verify full pipeline funnel calculation across multiple stages and deals."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)

    stage1 = DealStage(name="Lead", display_order=1)
    stage2 = DealStage(name="Proposal", display_order=2)
    test_session.add_all([stage1, stage2])
    test_session.commit()

    d1 = Deal(
        title="Alpha Deal",
        monetary_value=Decimal("50000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_1",
        win_probability=0.25,
        created_at=ref_time - timedelta(days=4),
    )
    d2 = Deal(
        title="Beta Deal",
        monetary_value=Decimal("100000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_1",
        win_probability=0.35,
        created_at=ref_time - timedelta(days=2),
    )
    d3 = Deal(
        title="Gamma Deal",
        monetary_value=Decimal("200000.00"),
        stage_id=stage2.id,
        assigned_team_member_id="usr_2",
        win_probability=0.75,
        created_at=ref_time - timedelta(days=1),
    )
    test_session.add_all([d1, d2, d3])
    test_session.commit()

    summary = get_pipeline_funnel_metrics(
        test_session,
        reference_time=ref_time,
    )

    assert isinstance(summary, PipelineFunnelSummary)
    assert summary.total_deals == 3
    assert summary.total_pipeline_value == 350000.00
    # Stage 1: 2 deals, Stage 2: 1 deal
    assert len(summary.stages) == 2

    stg1_metrics = summary.stages[0]
    assert stg1_metrics.stage_name == "Lead"
    assert stg1_metrics.deal_count == 2
    assert stg1_metrics.total_monetary_value == 150000.00
    assert stg1_metrics.average_monetary_value == 75000.00
    assert stg1_metrics.average_win_probability == 0.30
    assert stg1_metrics.average_deal_age_days == 3.0
    assert stg1_metrics.conversion_rate_from_previous == 100.0

    stg2_metrics = summary.stages[1]
    assert stg2_metrics.stage_name == "Proposal"
    assert stg2_metrics.deal_count == 1
    assert stg2_metrics.total_monetary_value == 200000.00
    assert stg2_metrics.conversion_rate_from_previous == 50.0


def test_get_pipeline_funnel_metrics_filtering(test_session: Session):
    """Verify filtering deals by team member, value range, and stage."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)

    stage1 = DealStage(name="Qualification", display_order=1)
    test_session.add(stage1)
    test_session.commit()

    d1 = Deal(
        title="Small Deal",
        monetary_value=Decimal("1000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_alice",
        created_at=ref_time - timedelta(days=10),
    )
    d2 = Deal(
        title="Big Deal",
        monetary_value=Decimal("100000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_bob",
        created_at=ref_time - timedelta(days=1),
    )
    test_session.add_all([d1, d2])
    test_session.commit()

    # Filter by team member
    alice_summary = get_pipeline_funnel_metrics(
        test_session,
        assigned_team_member_id="usr_alice",
        reference_time=ref_time,
    )
    assert alice_summary.total_deals == 1
    assert alice_summary.total_pipeline_value == 1000.00

    # Filter by min_value
    big_summary = get_pipeline_funnel_metrics(
        test_session,
        min_value=50000.00,
        reference_time=ref_time,
    )
    assert big_summary.total_deals == 1
    assert big_summary.total_pipeline_value == 100000.00

    # Filter by date range
    recent_summary = get_pipeline_funnel_metrics(
        test_session,
        start_date_filter=ref_time - timedelta(days=5),
        reference_time=ref_time,
    )
    assert recent_summary.total_deals == 1
    assert recent_summary.stages[0].deal_count == 1


def test_multi_criteria_filter_deals(test_session: Session):
    """Verify filter_deals with combined multi-criteria (date, value, stage, team member)."""
    ref_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    stage1 = DealStage(name="Stage 1", display_order=1)
    stage2 = DealStage(name="Stage 2", display_order=2)
    test_session.add_all([stage1, stage2])
    test_session.commit()

    d1 = Deal(
        title="Deal A",
        monetary_value=Decimal("25000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_alice",
        created_at=ref_time - timedelta(days=10),
    )
    d2 = Deal(
        title="Deal B",
        monetary_value=Decimal("75000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_alice",
        created_at=ref_time - timedelta(days=5),
    )
    d3 = Deal(
        title="Deal C",
        monetary_value=Decimal("150000.00"),
        stage_id=stage2.id,
        assigned_team_member_id="usr_bob",
        created_at=ref_time - timedelta(days=3),
    )
    d4 = Deal(
        title="Deal D",
        monetary_value=Decimal("80000.00"),
        stage_id=stage1.id,
        assigned_team_member_id="usr_alice",
        created_at=ref_time - timedelta(days=1),
    )
    test_session.add_all([d1, d2, d3, d4])
    test_session.commit()

    # Test filtering by date range (start_date_filter, end_date_filter)
    filtered = filter_deals(
        test_session,
        start_date_filter=ref_time - timedelta(days=6),
        end_date_filter=ref_time - timedelta(days=2),
    )
    assert len(filtered) == 2
    titles = {d.title for d in filtered}
    assert titles == {"Deal B", "Deal C"}

    # Test filtering by deal value range (min_value, max_value)
    filtered_val = filter_deals(
        test_session,
        min_value=50000.0,
        max_value=100000.0,
    )
    assert len(filtered_val) == 2
    assert {d.title for d in filtered_val} == {"Deal B", "Deal D"}

    # Test multi-criteria filter combining date, value, stage, and team member
    multi_filtered = filter_deals(
        test_session,
        start_date_filter=ref_time - timedelta(days=7),
        end_date_filter=ref_time,
        min_value=50000.0,
        max_value=100000.0,
        stage_id=stage1.id,
        assigned_team_member_id="usr_alice",
    )
    assert len(multi_filtered) == 2
    assert {d.title for d in multi_filtered} == {"Deal B", "Deal D"}

    # Test empty string ignored
    empty_str_filter = filter_deals(
        test_session,
        stage_id="",
        assigned_team_member_id="",
    )
    assert len(empty_str_filter) == 4
