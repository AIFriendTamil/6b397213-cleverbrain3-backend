"""FastAPI endpoint router and Pydantic validation schemas for lead acquisition source metrics and ROI analytics."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.lead import LeadSource
from src.models.session import Session as SessionModel
from src.services.analytics.roi_service import (
    get_acquisition_summary,
    get_lead_source_roi_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/acquisition", tags=["acquisition"])


# --- Schemas ---


class SourceROIMetricsSchema(BaseModel):
    """Schema for lead acquisition source performance and ROI metrics."""

    source_id: str = Field(..., description="Unique identifier for the lead acquisition source")
    source_name: str = Field(..., description="Name of the lead acquisition source")
    total_leads: int = Field(..., ge=0, description="Total number of leads acquired from this source")
    average_quality_score: float = Field(
        ..., ge=0.0, le=100.0, description="Average lead quality score (0 to 100)"
    )
    total_spend: float = Field(..., ge=0.0, description="Total cost/spend amount for this lead source")
    attributed_revenue: float = Field(
        ..., ge=0.0, description="Total attributed deal revenue generated from leads"
    )
    calculated_roi_percentage: float = Field(
        ..., description="Calculated Return on Investment (ROI) percentage"
    )

    model_config = ConfigDict(from_attributes=True)


class AcquisitionSummarySchema(BaseModel):
    """Schema for aggregate lead acquisition summary metrics across all sources."""

    total_sources: int = Field(..., ge=0, description="Total number of lead sources")
    total_leads: int = Field(..., ge=0, description="Aggregate total leads acquired across sources")
    overall_average_quality_score: float = Field(
        ..., ge=0.0, le=100.0, description="Overall average quality score across all leads"
    )
    total_spend: float = Field(..., ge=0.0, description="Aggregate total spend across all sources")
    total_attributed_revenue: float = Field(
        ..., ge=0.0, description="Aggregate total attributed deal revenue across all sources"
    )
    overall_roi_percentage: float = Field(
        ..., description="Overall aggregate Return on Investment (ROI) percentage"
    )
    sources: List[SourceROIMetricsSchema] = Field(
        default_factory=list, description="List of individual acquisition source metrics"
    )

    model_config = ConfigDict(from_attributes=True)


# --- Endpoints ---


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


@router.get(
    "/sources",
    response_model=List[SourceROIMetricsSchema],
    summary="Get acquisition source metrics and ROI",
    description="Retrieve lead counts, average quality scores, spend, attributed revenue, and calculated ROI percentage for lead acquisition sources.",
)
def get_acquisition_sources(
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    source_id: Optional[str] = Query(
        None, description="Optional lead source UUID filter"
    ),
    region_name: Optional[str] = Query(
        None, description="Optional geographic region name filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[SourceROIMetricsSchema]:
    """Expose acquisition source metrics and ROI analysis.

    Validates analytical inputs and enforces session authentication (NFR-3, NFR-5).
    """
    _validate_date_range(start_timestamp, end_timestamp)

    metrics_list = get_lead_source_roi_metrics(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
        region_name=region_name,
        campaign_id=campaign_id,
    )

    return [SourceROIMetricsSchema(**m.to_dict()) for m in metrics_list]


@router.get(
    "/summary",
    response_model=AcquisitionSummarySchema,
    summary="Get acquisition summary and overall ROI",
    description="Retrieve aggregate acquisition summary metrics, total leads, total spend, revenue, and overall ROI across sources.",
)
def get_acquisition_summary_metrics(
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    region_name: Optional[str] = Query(
        None, description="Optional geographic region name filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> AcquisitionSummarySchema:
    """Expose aggregate lead acquisition summary metrics."""
    _validate_date_range(start_timestamp, end_timestamp)

    summary_dict = get_acquisition_summary(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        region_name=region_name,
        campaign_id=campaign_id,
    )

    summary_dict["sources"] = [
        SourceROIMetricsSchema(**m.to_dict()) for m in summary_dict["sources"]
    ]
    return AcquisitionSummarySchema(**summary_dict)


@router.get(
    "/sources/{source_id}",
    response_model=SourceROIMetricsSchema,
    summary="Get acquisition source metrics by source ID",
    description="Retrieve detailed performance metrics and calculated ROI for a specific lead acquisition source by ID.",
)
def get_acquisition_source_by_id(
    source_id: str,
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for lead creation"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for lead creation"
    ),
    region_name: Optional[str] = Query(
        None, description="Optional geographic region name filter"
    ),
    campaign_id: Optional[str] = Query(
        None, description="Optional campaign UUID filter"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> SourceROIMetricsSchema:
    """Expose single acquisition source metrics by ID."""
    _validate_date_range(start_timestamp, end_timestamp)

    source_entity = db.get(LeadSource, source_id)
    if not source_entity:
        logger.warning("Lead source with ID '%s' not found.", source_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SOURCE_NOT_FOUND",
                "message": f"Lead source with id '{source_id}' was not found.",
                "details": {"source_id": source_id},
            },
        )

    metrics_list = get_lead_source_roi_metrics(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
        region_name=region_name,
        campaign_id=campaign_id,
    )

    if not metrics_list:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SOURCE_NOT_FOUND",
                "message": f"Lead source with id '{source_id}' was not found.",
                "details": {"source_id": source_id},
            },
        )

    return SourceROIMetricsSchema(**metrics_list[0].to_dict())
