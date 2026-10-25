"""Unit tests for ForecastingService in src/services/deals/forecasting_service.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.deal import Deal, DealStage, Milestone, MilestoneStatus
from src.services.deals.forecasting_port import (
    DealForecastResult,
    ForecastingAdapterUnavailableError,
    ForecastingError,
    ForecastingPort,
    MilestoneForecastEstimate,
)
from src.services.deals.forecasting_service import (
    ForecastingService,
    calculate_historical_deal_velocity,
    compute_fallback_forecast,
    get_deal_timeline_forecast,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


class MockSuccessPort(ForecastingPort):
    """Mock forecasting port returning a successful prediction."""

    def predict_timeline(
        self,
        deal_id: str,
        include_milestones: bool = True,
        **kwargs,
    ) -> DealForecastResult:
        close_dt = datetime(2026, 12, 1, 12, 0, 0, tzinfo=timezone.utc)
        ms_dt = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        estimates = []
        if include_milestones:
            estimates.append(
                MilestoneForecastEstimate(
                    milestone_id="ms_001",
                    milestone_title="Initial Review",
                    estimated_completion_date=ms_dt,
                    confidence_score=0.9,
                )
            )
        return DealForecastResult(
            deal_id=deal_id,
            predicted_close_date=close_dt,
            confidence_score=0.88,
            milestone_estimates=estimates,
            is_fallback=False,
            model_version="mock-ai-v1",
        )


class MockFailingPort(ForecastingPort):
    """Mock forecasting port raising an adapter error."""

    def predict_timeline(
        self,
        deal_id: str,
        include_milestones: bool = True,
        **kwargs,
    ) -> DealForecastResult:
        raise ForecastingAdapterUnavailableError("External AI service unreachable")


def test_predict_deal_timeline_empty_id(test_session: Session):
    """Verify ValueError is raised when empty deal_id is provided."""
    service = ForecastingService(session=test_session)
    with pytest.raises(ValueError, match="Deal ID must not be empty"):
        service.predict_deal_timeline("")

    with pytest.raises(ValueError, match="Deal ID must not be empty"):
        service.predict_deal_timeline("   ")


def test_predict_deal_timeline_not_found(test_session: Session):
    """Verify ValueError is raised when deal is not found in database."""
    service = ForecastingService(session=test_session)
    with pytest.raises(ValueError, match="not found"):
        service.predict_deal_timeline("non_existent_deal_id")


def test_predict_deal_timeline_success_via_port(test_session: Session):
    """Verify successful timeline forecasting when primary port succeeds."""
    stage = DealStage(name="Negotiation", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Enterprise License Deal",
        monetary_value=Decimal("50000.00"),
        stage_id=stage.id,
        win_probability=0.7,
    )
    test_session.add(deal)
    test_session.commit()

    service = ForecastingService(
        session=test_session,
        forecasting_port=MockSuccessPort(),
    )

    result = service.predict_deal_timeline(deal.id, include_milestones=True)

    assert result.deal_id == deal.id
    assert result.is_fallback is False
    assert result.model_version == "mock-ai-v1"
    assert result.confidence_score == 0.88
    assert len(result.milestone_estimates) == 1
    assert result.milestone_estimates[0].milestone_id == "ms_001"


def test_predict_deal_timeline_fallback_on_adapter_failure(test_session: Session):
    """Verify fallback forecast is returned when primary forecasting port fails."""
    stage1 = DealStage(name="Qualified", display_order=1)
    stage2 = DealStage(name="Closed Won", display_order=2)
    test_session.add_all([stage1, stage2])
    test_session.commit()

    deal = Deal(
        title="Healthcare Software Subscription",
        monetary_value=Decimal("120000.00"),
        stage_id=stage1.id,
        win_probability=0.5,
    )
    test_session.add(deal)
    test_session.commit()

    ref_time = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

    ms = Milestone(
        deal_id=deal.id,
        title="Contract Legal Review",
        target_date=datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc),
        status=MilestoneStatus.PENDING,
    )
    test_session.add(ms)
    test_session.commit()

    service = ForecastingService(
        session=test_session,
        forecasting_port=MockFailingPort(),
    )

    result = service.predict_deal_timeline(deal.id, include_milestones=True)

    assert result.deal_id == deal.id
    assert result.is_fallback is True
    assert result.model_version == "historical-velocity-v1"
    assert "historical_velocity" in result.details["calculation_method"]
    assert len(result.milestone_estimates) == 1
    assert result.milestone_estimates[0].milestone_id == ms.id
    assert result.milestone_estimates[0].milestone_title == "Contract Legal Review"


def test_calculate_historical_deal_velocity_empty_db(test_session: Session):
    """Verify default velocity fallback when database contains no deals."""
    velocity = calculate_historical_deal_velocity(test_session)
    assert velocity == 30.0


def test_calculate_historical_deal_velocity_with_completed_deals(test_session: Session):
    """Verify historical velocity calculation from completed deals."""
    stage = DealStage(name="Closed Won", display_order=10)
    test_session.add(stage)
    test_session.commit()

    created_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    updated_dt = datetime(2026, 1, 11, 0, 0, 0, tzinfo=timezone.utc)  # 10 days

    d1 = Deal(
        title="Completed Deal 1",
        monetary_value=Decimal("10000.00"),
        stage_id=stage.id,
        win_probability=1.0,
        created_at=created_dt,
        updated_at=updated_dt,
    )
    test_session.add(d1)
    test_session.commit()

    velocity = calculate_historical_deal_velocity(test_session)
    assert velocity == 10.0


def test_compute_fallback_forecast_milestone_statuses(test_session: Session):
    """Verify milestone estimate calculations across completed, upcoming, and overdue milestones."""
    stage = DealStage(name="In Progress", display_order=1)
    test_session.add(stage)
    test_session.commit()

    ref_time = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

    deal = Deal(
        title="Multi-Milestone Deal",
        monetary_value=Decimal("25000.00"),
        stage_id=stage.id,
        win_probability=0.4,
        created_at=datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc),
    )
    test_session.add(deal)
    test_session.commit()

    ms_completed = Milestone(
        deal_id=deal.id,
        title="Kickoff Meeting",
        target_date=datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc),
        completed_date=datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc),
        status=MilestoneStatus.COMPLETED,
    )
    ms_upcoming = Milestone(
        deal_id=deal.id,
        title="System Architecture Signoff",
        target_date=datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc),
        status=MilestoneStatus.IN_PROGRESS,
    )
    ms_overdue = Milestone(
        deal_id=deal.id,
        title="Initial Security Audit",
        target_date=datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc),
        status=MilestoneStatus.DELAYED,
    )
    test_session.add_all([ms_completed, ms_upcoming, ms_overdue])
    test_session.commit()

    result = compute_fallback_forecast(
        session=test_session,
        deal=deal,
        include_milestones=True,
        reference_time=ref_time,
    )

    assert result.is_fallback is True
    assert len(result.milestone_estimates) == 3

    estimates_by_id = {ms.milestone_id: ms for ms in result.milestone_estimates}

    # Completed milestone uses completed_date and confidence 1.0
    ms_comp_est = estimates_by_id[ms_completed.id]
    assert ms_comp_est.confidence_score == 1.0
    assert ms_comp_est.estimated_completion_date == ms_completed.completed_date

    # Upcoming milestone keeps target_date
    ms_up_est = estimates_by_id[ms_upcoming.id]
    assert ms_up_est.confidence_score == 0.6
    assert ms_up_est.estimated_completion_date == ms_upcoming.target_date

    # Overdue milestone gets shifted relative to ref_time
    ms_over_est = estimates_by_id[ms_overdue.id]
    assert ms_over_est.confidence_score == 0.6
    assert ms_over_est.estimated_completion_date > ref_time


def test_get_deal_timeline_forecast_convenience_function(test_session: Session):
    """Verify get_deal_timeline_forecast helper function."""
    stage = DealStage(name="Stage A", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Convenience Function Deal",
        monetary_value=Decimal("15000.00"),
        stage_id=stage.id,
        win_probability=0.6,
    )
    test_session.add(deal)
    test_session.commit()

    result = get_deal_timeline_forecast(
        session=test_session,
        deal_id=deal.id,
        forecasting_port=MockSuccessPort(),
    )

    assert result.deal_id == deal.id
    assert result.is_fallback is False
