"""Timeline forecasting service with historical velocity calculation and fallback handling."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.connectors.forecasting.adapter import ForecastingAdapter
from src.models.deal import Deal, DealStage, Milestone, MilestoneStatus
from src.services.deals.forecasting_port import (
    DealForecastResult,
    ForecastingAdapterUnavailableError,
    ForecastingError,
    ForecastingPort,
    MilestoneForecastEstimate,
)
from src.services.deals.pipeline_service import calculate_deal_age_days

logger = logging.getLogger(__name__)


def calculate_historical_deal_velocity(
    session: Session,
    reference_time: Optional[datetime] = None,
) -> float:
    """Calculate average historical deal duration in days across completed or existing deals.

    Args:
        session: Synchronous SQLAlchemy database session.
        reference_time: Reference timestamp (defaults to current UTC time).

    Returns:
        Average historical deal duration in days (float, minimum 1.0).
    """
    ref_time = reference_time or datetime.now(timezone.utc)

    # Query closed or high win-probability deals
    completed_deals = (
        session.query(Deal)
        .filter((Deal.win_probability >= 0.99) | (Deal.win_probability == 1.0))
        .all()
    )

    if completed_deals:
        durations = [
            calculate_deal_age_days(d.created_at, reference_time=d.updated_at or ref_time)
            for d in completed_deals
        ]
        if durations:
            avg_duration = sum(durations) / len(durations)
            return max(1.0, float(avg_duration))

    # Fallback to average age across all deals in database
    all_deals = session.query(Deal).all()
    if all_deals:
        ages = [calculate_deal_age_days(d.created_at, reference_time=ref_time) for d in all_deals]
        if ages:
            avg_age = sum(ages) / len(ages)
            return max(14.0, float(avg_age))

    # Default velocity fallback if no deals exist in database
    return 30.0


def compute_fallback_forecast(
    session: Session,
    deal: Deal,
    include_milestones: bool = True,
    fallback_reason: Optional[str] = None,
    reference_time: Optional[datetime] = None,
) -> DealForecastResult:
    """Compute timeline forecast using historical deal velocity and stage progression heuristics.

    Args:
        session: Synchronous SQLAlchemy database session.
        deal: Deal ORM entity to forecast.
        include_milestones: Whether to include milestone completion estimates.
        fallback_reason: Optional human-readable reason why fallback was executed.
        reference_time: Optional evaluation timestamp (defaults to current UTC time).

    Returns:
        DealForecastResult marked with is_fallback=True.
    """
    ref_time = reference_time or datetime.now(timezone.utc)

    # Retrieve all stages ordered by display_order
    stages = session.query(DealStage).order_by(DealStage.display_order.asc()).all()
    num_stages = len(stages)

    current_stage_idx = 0
    if stages and deal.stage_id:
        for idx, stg in enumerate(stages):
            if stg.id == deal.stage_id:
                current_stage_idx = idx
                break

    avg_total_days = calculate_historical_deal_velocity(session, reference_time=ref_time)

    if num_stages > 0:
        remaining_stages = max(1, num_stages - current_stage_idx)
        stage_fraction = remaining_stages / float(num_stages)
    else:
        stage_fraction = 0.5

    current_age_days = calculate_deal_age_days(deal.created_at, reference_time=ref_time)
    expected_remaining_days = max(1.0, avg_total_days * stage_fraction)

    predicted_close_dt = ref_time + timedelta(days=expected_remaining_days)

    milestone_estimates: list[MilestoneForecastEstimate] = []
    if include_milestones and deal.milestones:
        for ms in deal.milestones:
            if ms.status == MilestoneStatus.COMPLETED and ms.completed_date:
                ms_est_dt = ms.completed_date
                ms_confidence = 1.0
            elif ms.target_date:
                if ms.target_date > ref_time:
                    ms_est_dt = ms.target_date
                else:
                    ms_est_dt = ref_time + timedelta(days=3.0)
                ms_confidence = 0.6
            else:
                ms_est_dt = predicted_close_dt
                ms_confidence = 0.5

            milestone_estimates.append(
                MilestoneForecastEstimate(
                    milestone_id=str(ms.id),
                    milestone_title=ms.title,
                    estimated_completion_date=ms_est_dt,
                    confidence_score=ms_confidence,
                )
            )

    sample_size = session.query(Deal).count()
    fallback_confidence = 0.7 if sample_size >= 5 else 0.5

    return DealForecastResult(
        deal_id=str(deal.id),
        predicted_close_date=predicted_close_dt,
        confidence_score=fallback_confidence,
        milestone_estimates=milestone_estimates,
        is_fallback=True,
        model_version="historical-velocity-v1",
        details={
            "fallback_reason": fallback_reason or "Primary forecasting engine unavailable",
            "calculation_method": "historical_velocity",
            "sample_size": sample_size,
            "average_deal_duration_days": avg_total_days,
        },
    )


class ForecastingService:
    """Domain service managing timeline forecasting with primary adapter and fallback support."""

    def __init__(
        self,
        session: Session,
        forecasting_port: Optional[ForecastingPort] = None,
    ) -> None:
        """Initialize ForecastingService.

        Args:
            session: Synchronous SQLAlchemy database session.
            forecasting_port: Optional ForecastingPort implementation. If None,
                instantiates default ForecastingAdapter.
        """
        self.session = session
        self.forecasting_port = forecasting_port

    def predict_deal_timeline(
        self,
        deal_id: str,
        include_milestones: bool = True,
        **kwargs: Any,
    ) -> DealForecastResult:
        """Predict timeline for a deal using primary port or historical velocity fallback.

        Args:
            deal_id: UUID of target deal.
            include_milestones: Whether to include milestone completion estimates.
            **kwargs: Additional contextual parameters for forecasting.

        Returns:
            DealForecastResult containing predicted close date and milestone estimates.

        Raises:
            ValueError: If deal_id is empty or target deal is not found.
        """
        if not deal_id or not deal_id.strip():
            raise ValueError("Deal ID must not be empty.")

        deal = self.session.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise ValueError(f"Deal with ID '{deal_id}' not found.")

        port = self.forecasting_port
        if port is None:
            try:
                port = ForecastingAdapter()
            except Exception as exc:
                logger.warning(
                    "Failed to initialize default ForecastingAdapter for deal %s: %s",
                    deal_id,
                    exc,
                )
                port = None

        if port is not None:
            try:
                result = port.predict_timeline(
                    deal_id=deal_id,
                    include_milestones=include_milestones,
                    **kwargs,
                )
                logger.info(
                    "Successfully generated timeline forecast via port for deal %s",
                    deal_id,
                )
                return result
            except (ForecastingError, ForecastingAdapterUnavailableError, Exception) as exc:
                logger.warning(
                    "Primary forecasting port failed for deal %s, using historical fallback: %s",
                    deal_id,
                    exc,
                )

        return compute_fallback_forecast(
            session=self.session,
            deal=deal,
            include_milestones=include_milestones,
            fallback_reason="Primary forecasting engine unavailable or returned error",
        )


def get_deal_timeline_forecast(
    session: Session,
    deal_id: str,
    include_milestones: bool = True,
    forecasting_port: Optional[ForecastingPort] = None,
    **kwargs: Any,
) -> DealForecastResult:
    """Convenience function to generate a deal timeline forecast using ForecastingService.

    Args:
        session: Synchronous SQLAlchemy database session.
        deal_id: UUID of target deal.
        include_milestones: Whether to include milestone completion estimates.
        forecasting_port: Optional ForecastingPort adapter instance.
        **kwargs: Contextual forecasting parameters.

    Returns:
        DealForecastResult prediction.
    """
    service = ForecastingService(session=session, forecasting_port=forecasting_port)
    return service.predict_deal_timeline(
        deal_id=deal_id,
        include_milestones=include_milestones,
        **kwargs,
    )
