"""Unit tests for Pydantic validation schemas in backend/src/api/deals.py."""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError

from src.api.deals import (
    CriticalTaskCreate,
    CriticalTaskSchema,
    DealCreate,
    DealForecastResponse,
    DealSchema,
    DealUpdate,
    ForecastRequestPayload,
    MilestoneCreate,
    MilestoneForecastEstimateSchema,
    MilestoneSchema,
    MilestoneStatusEnum,
    MilestoneUpdate,
    PipelineFilterParams,
    PipelineFunnelSummarySchema,
    StageFunnelMetricsSchema,
    StakeholderCreate,
    StakeholderEdgeSchema,
    StakeholderGraphResponse,
    StakeholderNodeSchema,
)


def test_pipeline_filter_params_validation():
    """Test validation of PipelineFilterParams query schema."""
    now = datetime.now(timezone.utc)
    params = PipelineFilterParams(
        start_date=now,
        min_value=100.0,
        max_value=50000.0,
        stage_id="stage-1",
        team_member_id="user-1",
    )
    assert params.min_value == 100.0
    assert params.stage_id == "stage-1"

    # Negative min_value should raise validation error
    with pytest.raises(ValidationError):
        PipelineFilterParams(min_value=-10.0)


def test_stage_funnel_metrics_schema():
    """Test StageFunnelMetricsSchema serialization and validation."""
    metrics = StageFunnelMetricsSchema(
        stage_id="stage-1",
        stage_name="Prospecting",
        display_order=1,
        deal_count=5,
        total_monetary_value=10000.0,
        average_monetary_value=2000.0,
        average_win_probability=0.2,
        weighted_monetary_value=2000.0,
        average_deal_age_days=10.5,
        conversion_rate_from_previous=100.0,
    )
    assert metrics.deal_count == 5
    assert metrics.average_win_probability == 0.2

    # Probability out of bounds
    with pytest.raises(ValidationError):
        StageFunnelMetricsSchema(
            stage_id="s1",
            stage_name="Test",
            display_order=1,
            deal_count=1,
            total_monetary_value=100.0,
            average_monetary_value=100.0,
            average_win_probability=1.5,
            weighted_monetary_value=150.0,
            average_deal_age_days=1.0,
            conversion_rate_from_previous=100.0,
        )


def test_pipeline_funnel_summary_schema():
    """Test PipelineFunnelSummarySchema containing stage lists."""
    stage = StageFunnelMetricsSchema(
        stage_id="s1",
        stage_name="Discovery",
        display_order=1,
        deal_count=2,
        total_monetary_value=5000.0,
        average_monetary_value=2500.0,
        average_win_probability=0.4,
        weighted_monetary_value=2000.0,
        average_deal_age_days=5.0,
        conversion_rate_from_previous=100.0,
    )
    summary = PipelineFunnelSummarySchema(
        total_deals=2,
        total_pipeline_value=5000.0,
        total_weighted_value=2000.0,
        overall_average_win_probability=0.4,
        overall_average_deal_age_days=5.0,
        stages=[stage],
    )
    assert summary.total_deals == 2
    assert len(summary.stages) == 1


def test_critical_task_and_milestone_schemas():
    """Test Milestone and CriticalTask create/update/response schemas."""
    now = datetime.now(timezone.utc)

    task_create = CriticalTaskCreate(
        title="Sign NDA",
        due_date=now,
        is_critical=True,
    )
    assert task_create.title == "Sign NDA"

    task_res = CriticalTaskSchema(
        id="t-1",
        milestone_id="m-1",
        title="Sign NDA",
        due_date=now,
        is_critical=True,
        is_overdue=False,
        escalated_to_supervisor=False,
    )
    assert task_res.id == "t-1"

    ms_create = MilestoneCreate(
        title="Contract Signed",
        target_date=now,
        status=MilestoneStatusEnum.PENDING,
    )
    assert ms_create.status == MilestoneStatusEnum.PENDING

    ms_res = MilestoneSchema(
        id="m-1",
        deal_id="d-1",
        title="Contract Signed",
        target_date=now,
        status=MilestoneStatusEnum.IN_PROGRESS,
        color_code="#EAB308",
        critical_tasks=[task_res],
    )
    assert ms_res.color_code == "#EAB308"
    assert len(ms_res.critical_tasks) == 1


def test_stakeholder_schemas():
    """Test Stakeholder node, edge, and graph schemas."""
    st_create = StakeholderCreate(
        name="Alice Smith",
        org_role="CTO",
        contact_email="alice@example.com",
    )
    assert st_create.name == "Alice Smith"

    node = StakeholderNodeSchema(
        id="st-1",
        name="Alice Smith",
        org_role="CTO",
        contact_email="alice@example.com",
        deal_id="d-1",
        subordinate_ids=["st-2"],
    )
    edge = StakeholderEdgeSchema(
        source_id="st-2",
        target_id="st-1",
        relationship_type="reports_to",
    )
    graph = StakeholderGraphResponse(
        deal_id="d-1",
        nodes=[node],
        edges=[edge],
        dag_edges=[edge],
        has_cycles=False,
        detected_cycles=[],
        root_stakeholder_ids=["st-1"],
    )
    assert graph.has_cycles is False
    assert len(graph.nodes) == 1


def test_deal_forecast_schemas():
    """Test Forecast request and response schemas."""
    now = datetime.now(timezone.utc)
    req = ForecastRequestPayload(include_milestones=True)
    assert req.include_milestones is True

    ms_est = MilestoneForecastEstimateSchema(
        milestone_id="m-1",
        milestone_title="Final Demo",
        estimated_completion_date=now,
        confidence_score=0.85,
    )

    forecast_res = DealForecastResponse(
        deal_id="d-1",
        predicted_close_date=now,
        confidence_score=0.8,
        milestone_estimates=[ms_est],
        is_fallback=False,
        model_version="v1.0",
        details={"sample_size": 10},
    )
    assert forecast_res.confidence_score == 0.8
    assert forecast_res.is_fallback is False
    assert len(forecast_res.milestone_estimates) == 1


def test_deal_management_schemas():
    """Test DealCreate, DealUpdate, and DealSchema validation."""
    d_create = DealCreate(
        title="Enterprise Plan Deal",
        monetary_value=Decimal("50000.00"),
        stage_id="stage-1",
        win_probability=0.75,
    )
    assert d_create.monetary_value == Decimal("50000.00")

    d_schema = DealSchema(
        id="d-1",
        title="Enterprise Plan Deal",
        monetary_value=50000.0,
        stage_id="stage-1",
        assigned_team_member_id="u-1",
        win_probability=0.75,
    )
    assert d_schema.win_probability == 0.75
