"""Pipeline funnel metrics calculation, win probability aggregation, and filtering service."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.models.deal import Deal, DealStage

logger = logging.getLogger(__name__)


@dataclass
class StageFunnelMetrics:
    """Aggregated funnel metrics for a single deal stage."""

    stage_id: str
    stage_name: str
    display_order: int
    deal_count: int
    total_monetary_value: float
    average_monetary_value: float
    average_win_probability: float
    weighted_monetary_value: float
    average_deal_age_days: float
    conversion_rate_from_previous: float

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics instance to dictionary format."""
        return asdict(self)


@dataclass
class PipelineFunnelSummary:
    """Aggregated pipeline summary metrics across all stages."""

    total_deals: int
    total_pipeline_value: float
    total_weighted_value: float
    overall_average_win_probability: float
    overall_average_deal_age_days: float
    stages: list[StageFunnelMetrics]

    def to_dict(self) -> dict[str, Any]:
        """Convert summary instance to dictionary format."""
        return {
            "total_deals": self.total_deals,
            "total_pipeline_value": self.total_pipeline_value,
            "total_weighted_value": self.total_weighted_value,
            "overall_average_win_probability": self.overall_average_win_probability,
            "overall_average_deal_age_days": self.overall_average_deal_age_days,
            "stages": [stage.to_dict() for stage in self.stages],
        }


def calculate_deal_age_days(
    created_at: datetime,
    reference_time: Optional[datetime] = None,
) -> float:
    """Calculate the age of a deal in fractional days relative to reference_time.

    Args:
        created_at: Datetime when deal was created.
        reference_time: Evaluation timestamp (defaults to current UTC time).

    Returns:
        Age of the deal in days as a non-negative float.
    """
    if reference_time is None:
        ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = reference_time

    if created_at.tzinfo is None and ref_dt.tzinfo is not None:
        created_dt = created_at.replace(tzinfo=timezone.utc)
    elif created_at.tzinfo is not None and ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
    else:
        created_dt = created_at

    delta_seconds = (ref_dt - created_dt).total_seconds()
    if delta_seconds < 0:
        return 0.0

    return delta_seconds / 86400.0


def aggregate_win_probabilities(deals: list[Deal]) -> dict[str, float]:
    """Aggregate win probability metrics across a collection of deals.

    Args:
        deals: List of Deal ORM entities.

    Returns:
        Dictionary containing win probability statistics and weighted values.
    """
    if not deals:
        return {
            "average_win_probability": 0.0,
            "min_win_probability": 0.0,
            "max_win_probability": 0.0,
            "total_weighted_value": 0.0,
        }

    total_deals = len(deals)
    probabilities = [float(deal.win_probability) for deal in deals]
    avg_prob = sum(probabilities) / total_deals
    min_prob = min(probabilities)
    max_prob = max(probabilities)

    weighted_value = sum(
        float(deal.monetary_value) * float(deal.win_probability)
        for deal in deals
    )

    return {
        "average_win_probability": round(avg_prob, 4),
        "min_win_probability": round(min_prob, 4),
        "max_win_probability": round(max_prob, 4),
        "total_weighted_value": round(weighted_value, 2),
    }


def calculate_conversion_rate(
    current_count: int,
    previous_count: Optional[int],
) -> float:
    """Calculate conversion rate percentage from previous stage deal count.

    Args:
        current_count: Number of deals in the current stage.
        previous_count: Number of deals in the preceding stage, or None if first stage.

    Returns:
        Conversion rate percentage rounded to 2 decimal places.
    """
    if previous_count is None:
        return 100.0 if current_count > 0 else 0.0
    if previous_count <= 0:
        return 0.0

    rate = (current_count / previous_count) * 100.0
    return round(rate, 2)


def calculate_stage_metrics(
    stage: DealStage,
    deals: list[Deal],
    reference_time: Optional[datetime] = None,
    previous_stage_deal_count: Optional[int] = None,
) -> StageFunnelMetrics:
    """Calculate aggregated funnel metrics for a single deal stage.

    Args:
        stage: DealStage ORM entity.
        deals: List of Deal entities belonging to this stage.
        reference_time: Timestamp to compute deal age against.
        previous_stage_deal_count: Deal count from preceding stage in display order.

    Returns:
        StageFunnelMetrics data instance.
    """
    deal_count = len(deals)
    if deal_count == 0:
        conversion = calculate_conversion_rate(0, previous_stage_deal_count)
        return StageFunnelMetrics(
            stage_id=stage.id,
            stage_name=stage.name,
            display_order=stage.display_order,
            deal_count=0,
            total_monetary_value=0.0,
            average_monetary_value=0.0,
            average_win_probability=0.0,
            weighted_monetary_value=0.0,
            average_deal_age_days=0.0,
            conversion_rate_from_previous=conversion,
        )

    total_val = sum(float(deal.monetary_value) for deal in deals)
    avg_val = total_val / deal_count
    win_stats = aggregate_win_probabilities(deals)
    total_age = sum(
        calculate_deal_age_days(deal.created_at, reference_time)
        for deal in deals
    )
    avg_age = total_age / deal_count
    conversion = calculate_conversion_rate(
        deal_count, previous_stage_deal_count
    )

    return StageFunnelMetrics(
        stage_id=stage.id,
        stage_name=stage.name,
        display_order=stage.display_order,
        deal_count=deal_count,
        total_monetary_value=round(total_val, 2),
        average_monetary_value=round(avg_val, 2),
        average_win_probability=win_stats["average_win_probability"],
        weighted_monetary_value=win_stats["total_weighted_value"],
        average_deal_age_days=round(avg_age, 2),
        conversion_rate_from_previous=conversion,
    )


def filter_deals_query(
    db: Session,
    start_date_filter: Optional[datetime] = None,
    end_date_filter: Optional[datetime] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    stage_id: Optional[str] = None,
    assigned_team_member_id: Optional[str] = None,
):
    """Construct a SQLAlchemy query for Deal records with multi-criteria filtering.

    Args:
        db: Synchronous SQLAlchemy database session.
        start_date_filter: Lower bound created_at datetime.
        end_date_filter: Upper bound created_at datetime.
        min_value: Minimum monetary value.
        max_value: Maximum monetary value.
        stage_id: DealStage ID filter.
        assigned_team_member_id: Team member user ID filter.

    Returns:
        SQLAlchemy Query object for Deal entities.
    """
    query = db.query(Deal)

    if start_date_filter is not None:
        query = query.filter(Deal.created_at >= start_date_filter)
    if end_date_filter is not None:
        query = query.filter(Deal.created_at <= end_date_filter)
    if min_value is not None:
        query = query.filter(Deal.monetary_value >= Decimal(str(min_value)))
    if max_value is not None:
        query = query.filter(Deal.monetary_value <= Decimal(str(max_value)))
    if stage_id is not None and stage_id != "":
        query = query.filter(Deal.stage_id == stage_id)
    if assigned_team_member_id is not None and assigned_team_member_id != "":
        query = query.filter(
            Deal.assigned_team_member_id == assigned_team_member_id
        )

    return query


def filter_deals(
    db: Session,
    start_date_filter: Optional[datetime] = None,
    end_date_filter: Optional[datetime] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    stage_id: Optional[str] = None,
    assigned_team_member_id: Optional[str] = None,
) -> list[Deal]:
    """Execute multi-criteria pipeline filtering for Deal records and return matching list.

    Args:
        db: Synchronous SQLAlchemy database session.
        start_date_filter: Lower bound created_at datetime.
        end_date_filter: Upper bound created_at datetime.
        min_value: Minimum monetary value.
        max_value: Maximum monetary value.
        stage_id: DealStage ID filter.
        assigned_team_member_id: Team member user ID filter.

    Returns:
        List of Deal entities matching all specified criteria.
    """
    return filter_deals_query(
        db=db,
        start_date_filter=start_date_filter,
        end_date_filter=end_date_filter,
        min_value=min_value,
        max_value=max_value,
        stage_id=stage_id,
        assigned_team_member_id=assigned_team_member_id,
    ).all()


def get_pipeline_funnel_metrics(
    db: Session,
    start_date_filter: Optional[datetime] = None,
    end_date_filter: Optional[datetime] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    stage_id: Optional[str] = None,
    assigned_team_member_id: Optional[str] = None,
    reference_time: Optional[datetime] = None,
) -> PipelineFunnelSummary:
    """Calculate and aggregate pipeline funnel metrics across all deal stages.

    Args:
        db: Synchronous SQLAlchemy database session.
        start_date_filter: Filter deals created on or after this timestamp.
        end_date_filter: Filter deals created on or before this timestamp.
        min_value: Filter deals with monetary_value >= min_value.
        max_value: Filter deals with monetary_value <= max_value.
        stage_id: Filter deals by specific stage ID.
        assigned_team_member_id: Filter deals by team member ID.
        reference_time: Timestamp for deal age calculation.

    Returns:
        PipelineFunnelSummary containing overall pipeline summary and stage metrics.
    """
    stages = (
        db.query(DealStage)
        .order_by(DealStage.display_order.asc(), DealStage.name.asc())
        .all()
    )

    query = filter_deals_query(
        db,
        start_date_filter=start_date_filter,
        end_date_filter=end_date_filter,
        min_value=min_value,
        max_value=max_value,
        stage_id=stage_id,
        assigned_team_member_id=assigned_team_member_id,
    )
    all_filtered_deals = query.all()

    deals_by_stage: dict[str, list[Deal]] = {}
    for deal in all_filtered_deals:
        deals_by_stage.setdefault(deal.stage_id, []).append(deal)

    stage_metrics_list: list[StageFunnelMetrics] = []
    previous_count: Optional[int] = None

    for stage in stages:
        stage_deals = deals_by_stage.get(stage.id, [])
        metrics = calculate_stage_metrics(
            stage=stage,
            deals=stage_deals,
            reference_time=reference_time,
            previous_stage_deal_count=previous_count,
        )
        stage_metrics_list.append(metrics)
        previous_count = metrics.deal_count

    total_deals = len(all_filtered_deals)
    if total_deals == 0:
        return PipelineFunnelSummary(
            total_deals=0,
            total_pipeline_value=0.0,
            total_weighted_value=0.0,
            overall_average_win_probability=0.0,
            overall_average_deal_age_days=0.0,
            stages=stage_metrics_list,
        )

    total_pipeline_val = sum(
        float(deal.monetary_value) for deal in all_filtered_deals
    )
    win_stats = aggregate_win_probabilities(all_filtered_deals)
    total_age = sum(
        calculate_deal_age_days(deal.created_at, reference_time)
        for deal in all_filtered_deals
    )
    overall_avg_age = total_age / total_deals

    return PipelineFunnelSummary(
        total_deals=total_deals,
        total_pipeline_value=round(total_pipeline_val, 2),
        total_weighted_value=win_stats["total_weighted_value"],
        overall_average_win_probability=win_stats["average_win_probability"],
        overall_average_deal_age_days=round(overall_avg_age, 2),
        stages=stage_metrics_list,
    )
