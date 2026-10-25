"""Domain service for conversion funnel stage progression, rate math, and bottleneck detection."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.funnel import FunnelStage
from src.models.lead import Lead

logger = logging.getLogger(__name__)


def calculate_stage_conversion_rate(
    current_count: int,
    previous_count: Optional[int],
) -> float:
    """Calculate conversion rate percentage from preceding stage lead count.

    Formula:
        For first stage (previous_count is None): 100.0% if current_count > 0 else 0.0%
        For subsequent stages: (current_count / previous_count) * 100.0

    Args:
        current_count: Number of leads in current stage.
        previous_count: Number of leads in preceding stage.

    Returns:
        Conversion rate percentage rounded to 2 decimal places.
    """
    if previous_count is None:
        return 100.0 if current_count > 0 else 0.0

    if previous_count <= 0:
        return 0.0

    rate = (current_count / previous_count) * 100.0
    return round(rate, 2)


def detect_stage_bottleneck(
    conversion_rate_percentage: float,
    target_conversion_rate: float | Decimal | int,
    default_threshold: Optional[float] = None,
) -> bool:
    """Determine if a stage's conversion rate falls below target bottleneck threshold.

    Args:
        conversion_rate_percentage: Current stage conversion rate.
        target_conversion_rate: Stage target conversion rate threshold.
        default_threshold: Fallback threshold if target is 0 or unconfigured.

    Returns:
        True if conversion rate falls strictly below threshold, False otherwise.
    """
    target_val = float(target_conversion_rate) if target_conversion_rate is not None else 0.0
    fallback = (
        default_threshold
        if default_threshold is not None
        else float(settings.FUNNEL_BOTTLENECK_TARGET_CONVERSION_RATE)
    )

    effective_target = target_val if target_val > 0.0 else fallback
    return conversion_rate_percentage < effective_target


def calculate_lead_time_in_stage(
    created_timestamp: datetime,
    reference_time: Optional[datetime] = None,
) -> float:
    """Calculate time elapsed in fractional days since lead creation timestamp.

    Args:
        created_timestamp: Datetime when lead was created.
        reference_time: Evaluation timestamp (defaults to current UTC time).

    Returns:
        Time in stage in days as a non-negative float rounded to 2 decimal places.
    """
    ref_dt = reference_time if reference_time is not None else datetime.now(timezone.utc)

    created_dt = created_timestamp
    if created_dt.tzinfo is None and ref_dt.tzinfo is not None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    elif created_dt.tzinfo is not None and ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=timezone.utc)

    delta_seconds = (ref_dt - created_dt).total_seconds()
    if delta_seconds < 0:
        return 0.0

    return round(delta_seconds / 86400.0, 2)


@dataclass
class FunnelStageMetrics:
    """Aggregated conversion metrics for a single funnel stage."""

    stage_id: str
    stage_name: str
    stage_order: int
    lead_count: int
    conversion_rate_percentage: float
    target_conversion_rate: float
    is_bottleneck: bool

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics instance to dictionary representation."""
        return asdict(self)


@dataclass
class StageLeadDetail:
    """Detailed lead record representation for funnel stage drill-down."""

    lead_id: str
    source_id: str
    campaign_id: Optional[str]
    quality_score: int
    status_stage: str
    region_name: str
    state_code: Optional[str]
    postal_code: Optional[str]
    deal_value: float
    created_timestamp: datetime
    time_in_stage_days: float

    def to_dict(self) -> dict[str, Any]:
        """Convert lead detail instance to dictionary representation."""
        d = asdict(self)
        if isinstance(d.get("created_timestamp"), datetime):
            d["created_timestamp"] = d["created_timestamp"].isoformat()
        return d


@dataclass
class StageLeadDrillDownResponse:
    """Paginated response container for stage lead drill-down."""

    stage_id: str
    stage_name: str
    total_leads: int
    page: int
    limit: int
    leads: List[StageLeadDetail]

    def to_dict(self) -> dict[str, Any]:
        """Convert response instance to dictionary representation."""
        return {
            "stage_id": self.stage_id,
            "stage_name": self.stage_name,
            "total_leads": self.total_leads,
            "page": self.page,
            "limit": self.limit,
            "leads": [lead.to_dict() for lead in self.leads],
        }


def _apply_lead_filters(
    query: Any,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
    source_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> Any:
    """Apply common timestamp and entity filters to a Lead query."""
    if start_timestamp is not None:
        query = query.filter(Lead.created_timestamp >= start_timestamp)
    if end_timestamp is not None:
        query = query.filter(Lead.created_timestamp <= end_timestamp)
    if source_id is not None:
        query = query.filter(Lead.source_id == source_id)
    if campaign_id is not None:
        query = query.filter(Lead.campaign_id == campaign_id)
    return query


def get_funnel_stage_metrics(
    db: Session,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
    source_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    default_target_threshold: Optional[float] = None,
) -> List[FunnelStageMetrics]:
    """Retrieve funnel stages ordered by stage_order and compute conversion metrics.

    Args:
        db: Synchronous SQLAlchemy database session.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.
        source_id: Optional UUID string filter for lead source.
        campaign_id: Optional UUID string filter for campaign.
        default_target_threshold: Optional bottleneck threshold override.

    Returns:
        List of FunnelStageMetrics ordered sequentially by stage_order.
    """
    stages = db.query(FunnelStage).order_by(FunnelStage.stage_order.asc()).all()
    if not stages:
        return []

    metrics_list: List[FunnelStageMetrics] = []
    previous_count: Optional[int] = None

    for stage in stages:
        lead_query = db.query(Lead).filter(
            or_(
                Lead.status_stage == stage.stage_id,
                Lead.status_stage == stage.stage_name,
            )
        )
        lead_query = _apply_lead_filters(
            lead_query, start_timestamp, end_timestamp, source_id, campaign_id
        )
        current_count = lead_query.count()

        conv_rate = calculate_stage_conversion_rate(current_count, previous_count)
        target_rate = float(stage.target_conversion_rate) if stage.target_conversion_rate else 0.0

        is_bottleneck = detect_stage_bottleneck(
            conv_rate,
            target_rate,
            default_threshold=default_target_threshold,
        )

        metrics_list.append(
            FunnelStageMetrics(
                stage_id=stage.stage_id,
                stage_name=stage.stage_name,
                stage_order=stage.stage_order,
                lead_count=current_count,
                conversion_rate_percentage=conv_rate,
                target_conversion_rate=target_rate,
                is_bottleneck=is_bottleneck,
            )
        )
        previous_count = current_count

    return metrics_list


def get_stage_lead_drilldown(
    db: Session,
    stage_id: str,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
    source_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    reference_time: Optional[datetime] = None,
) -> Optional[StageLeadDrillDownResponse]:
    """Retrieve paginated lead details for drill-down into a specific funnel stage.

    Args:
        db: Synchronous SQLAlchemy database session.
        stage_id: Stage UUID string or stage name identifier.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.
        source_id: Optional UUID string filter for lead source.
        campaign_id: Optional UUID string filter for campaign.
        page: Page index (1-based).
        limit: Number of records per page.
        reference_time: Optional timestamp to calculate time in stage against.

    Returns:
        StageLeadDrillDownResponse if stage exists, None if stage is not found.
    """
    stage = (
        db.query(FunnelStage)
        .filter(
            or_(
                FunnelStage.stage_id == stage_id,
                FunnelStage.stage_name == stage_id,
            )
        )
        .first()
    )
    if not stage:
        logger.warning("Funnel stage drill-down requested for non-existent stage_id: %s", stage_id)
        return None

    lead_query = db.query(Lead).filter(
        or_(
            Lead.status_stage == stage.stage_id,
            Lead.status_stage == stage.stage_name,
        )
    )
    lead_query = _apply_lead_filters(
        lead_query, start_timestamp, end_timestamp, source_id, campaign_id
    )

    total_leads = lead_query.count()

    norm_page = max(1, page)
    norm_limit = max(1, limit)
    offset_val = (norm_page - 1) * norm_limit

    leads = (
        lead_query.order_by(Lead.created_timestamp.desc())
        .offset(offset_val)
        .limit(norm_limit)
        .all()
    )

    ref_time = reference_time if reference_time is not None else datetime.now(timezone.utc)
    lead_details = [
        StageLeadDetail(
            lead_id=lead.lead_id,
            source_id=lead.source_id,
            campaign_id=lead.campaign_id,
            quality_score=lead.quality_score,
            status_stage=lead.status_stage,
            region_name=lead.region_name,
            state_code=lead.state_code,
            postal_code=lead.postal_code,
            deal_value=float(lead.deal_value) if lead.deal_value is not None else 0.0,
            created_timestamp=lead.created_timestamp,
            time_in_stage_days=calculate_lead_time_in_stage(
                lead.created_timestamp, ref_time
            ),
        )
        for lead in leads
    ]

    return StageLeadDrillDownResponse(
        stage_id=stage.stage_id,
        stage_name=stage.stage_name,
        total_leads=total_leads,
        page=norm_page,
        limit=norm_limit,
        leads=lead_details,
    )
