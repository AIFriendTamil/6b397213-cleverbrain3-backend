"""FastAPI endpoint router and Pydantic validation schemas for conversion funnel analytics and stage lead drill-down."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.services.analytics.funnel_service import (
    get_funnel_stage_metrics,
    get_stage_lead_drilldown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/funnel", tags=["funnel"])


# --- Validation Schemas ---


class FunnelStageMetricsSchema(BaseModel):
    """Schema for conversion metrics and bottleneck status of a single funnel stage."""

    stage_id: str = Field(..., description="Unique identifier for the funnel stage")
    stage_name: str = Field(..., description="Display name of the funnel stage")
    stage_order: int = Field(..., ge=1, description="Sequential ordering number of the stage")
    lead_count: int = Field(..., ge=0, description="Number of leads currently in this stage")
    conversion_rate_percentage: float = Field(
        ..., ge=0.0, le=100.0, description="Calculated conversion rate percentage from previous stage"
    )
    target_conversion_rate: float = Field(
        ..., ge=0.0, le=100.0, description="Target conversion rate threshold percentage"
    )
    is_bottleneck: bool = Field(
        ..., description="True if conversion rate falls below target bottleneck threshold"
    )

    model_config = ConfigDict(from_attributes=True)


class StageLeadDetailSchema(BaseModel):
    """Schema for individual lead details in funnel stage drill-down."""

    lead_id: str = Field(..., description="Unique identifier for the lead")
    source_id: str = Field(..., description="Lead acquisition source identifier")
    campaign_id: Optional[str] = Field(None, description="Associated marketing campaign identifier")
    quality_score: int = Field(..., ge=0, le=100, description="Lead quality score (0 to 100)")
    status_stage: str = Field(..., description="Current status/stage identifier of the lead")
    region_name: str = Field(..., description="Geographic region name")
    state_code: Optional[str] = Field(None, description="Two-letter state code")
    postal_code: Optional[str] = Field(None, description="Postal/ZIP code")
    deal_value: float = Field(..., ge=0.0, description="Monetary deal value")
    created_timestamp: datetime = Field(..., description="Lead creation timestamp")
    time_in_stage_days: float = Field(..., ge=0.0, description="Elapsed time in stage in fractional days")

    model_config = ConfigDict(from_attributes=True)


class StageLeadDrillDownResponseSchema(BaseModel):
    """Paginated response container schema for funnel stage lead drill-down."""

    stage_id: str = Field(..., description="Funnel stage identifier")
    stage_name: str = Field(..., description="Funnel stage display name")
    total_leads: int = Field(..., ge=0, description="Total number of leads in this stage matching filters")
    page: int = Field(..., ge=1, description="Current page number (1-based)")
    limit: int = Field(..., ge=1, le=100, description="Number of records per page")
    leads: List[StageLeadDetailSchema] = Field(
        default_factory=list, description="List of detailed lead records for current page"
    )

    model_config = ConfigDict(from_attributes=True)


# --- Helper Functions ---


def _validate_date_range(
    start_timestamp: Optional[datetime],
    end_timestamp: Optional[datetime],
) -> None:
    """Helper function to validate date/time range query parameters at the API boundary."""
    if start_timestamp and end_timestamp and start_timestamp > end_timestamp:
        logger.warning(
            "Invalid date range parameter: start_timestamp (%s) > end_timestamp (%s)",
            start_timestamp,
            end_timestamp,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_DATE_RANGE",
                "message": "start_timestamp cannot be greater than end_timestamp",
                "details": {
                    "start_timestamp": start_timestamp.isoformat(),
                    "end_timestamp": end_timestamp.isoformat(),
                },
            },
        )


# --- Endpoints ---


@router.get(
    "/stages",
    response_model=List[FunnelStageMetricsSchema],
    summary="Get conversion funnel stage metrics",
    description="Retrieve stage-by-stage lead counts, conversion rates, target thresholds, and operational bottleneck flags across the sales pipeline.",
)
def get_funnel_stages(
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    source_id: Optional[str] = Query(
        None, description="Optional lead source UUID filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    default_target_threshold: Optional[float] = Query(
        None, ge=0.0, le=100.0, description="Optional bottleneck threshold percentage override"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[FunnelStageMetricsSchema]:
    """Expose stage-by-stage conversion metrics and bottleneck indicators (FR-3, FR-4, FR-11, FR-12)."""
    _validate_date_range(start_timestamp, end_timestamp)

    metrics = get_funnel_stage_metrics(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
        campaign_id=campaign_id,
        default_target_threshold=default_target_threshold,
    )

    return [FunnelStageMetricsSchema(**m.to_dict()) for m in metrics]


@router.get(
    "/bottlenecks",
    response_model=List[FunnelStageMetricsSchema],
    summary="Get conversion funnel bottleneck stages",
    description="Retrieve only the pipeline conversion stages where conversion rate falls strictly below target threshold.",
)
def get_funnel_bottlenecks(
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    source_id: Optional[str] = Query(
        None, description="Optional lead source UUID filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    default_target_threshold: Optional[float] = Query(
        None, ge=0.0, le=100.0, description="Optional bottleneck threshold percentage override"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[FunnelStageMetricsSchema]:
    """Expose operational bottleneck stages falling below conversion targets (FR-4)."""
    _validate_date_range(start_timestamp, end_timestamp)

    metrics = get_funnel_stage_metrics(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
        campaign_id=campaign_id,
        default_target_threshold=default_target_threshold,
    )

    bottlenecks = [m for m in metrics if m.is_bottleneck]
    return [FunnelStageMetricsSchema(**m.to_dict()) for m in bottlenecks]


@router.get(
    "/stages/{stage_id}",
    response_model=FunnelStageMetricsSchema,
    summary="Get funnel stage metrics by stage ID",
    description="Retrieve conversion metrics and bottleneck status for a specific funnel stage by ID or stage name.",
)
def get_funnel_stage_by_id(
    stage_id: str,
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    source_id: Optional[str] = Query(
        None, description="Optional lead source UUID filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    default_target_threshold: Optional[float] = Query(
        None, ge=0.0, le=100.0, description="Optional bottleneck threshold percentage override"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> FunnelStageMetricsSchema:
    """Expose metrics for a single specified conversion stage."""
    _validate_date_range(start_timestamp, end_timestamp)

    metrics = get_funnel_stage_metrics(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
        campaign_id=campaign_id,
        default_target_threshold=default_target_threshold,
    )

    for m in metrics:
        if m.stage_id == stage_id or m.stage_name == stage_id:
            return FunnelStageMetricsSchema(**m.to_dict())

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "FUNNEL_STAGE_NOT_FOUND",
            "message": f"Funnel stage with ID or name '{stage_id}' was not found",
            "details": {"stage_id": stage_id},
        },
    )


@router.get(
    "/stages/{stage_id}/leads",
    response_model=StageLeadDrillDownResponseSchema,
    summary="Drill down into funnel stage lead details",
    description="Retrieve paginated lead details for leads currently residing in a specific conversion funnel stage.",
)
def get_funnel_stage_lead_drilldown(
    stage_id: str,
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    source_id: Optional[str] = Query(
        None, description="Optional lead source UUID filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(20, ge=1, le=100, description="Records per page (1 to 100)"),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> StageLeadDrillDownResponseSchema:
    """Expose drill-down detailed leads for a specific funnel stage (FR-5, FR-11, FR-12)."""
    _validate_date_range(start_timestamp, end_timestamp)

    result = get_stage_lead_drilldown(
        db,
        stage_id=stage_id,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
        campaign_id=campaign_id,
        page=page,
        limit=limit,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FUNNEL_STAGE_NOT_FOUND",
                "message": f"Funnel stage with ID or name '{stage_id}' was not found",
                "details": {"stage_id": stage_id},
            },
        )

    return StageLeadDrillDownResponseSchema(**result.to_dict())
