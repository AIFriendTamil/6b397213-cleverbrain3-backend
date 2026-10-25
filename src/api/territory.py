"""FastAPI endpoint router and Pydantic validation schemas for geographic territory opportunity density analytics."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.lib.config import settings
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.services.analytics.territory_service import (
    get_territory_density_metrics,
    get_unmapped_region_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/territory", tags=["territory"])


# --- Schemas ---


class TerritoryDensityMetricsSchema(BaseModel):
    """Schema for regional opportunity density and financial metrics."""

    region_name: str = Field(..., description="Name of the geographic region or 'Unmapped Region'")
    opportunity_count: int = Field(..., ge=0, description="Total number of opportunities/leads in region")
    total_deal_value: float = Field(..., ge=0.0, description="Sum of deal values in region")
    average_quality_score: float = Field(
        ..., ge=0.0, le=100.0, description="Average lead quality score (0 to 100)"
    )
    density_score: float = Field(
        ..., ge=0.0, le=100.0, description="Density score percentage relative to total opportunities"
    )
    latitude: float = Field(..., description="Geographic center latitude for heat map plotting")
    longitude: float = Field(..., description="Geographic center longitude for heat map plotting")

    model_config = ConfigDict(from_attributes=True)


# --- Helpers ---


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


def _validate_numeric_filters(
    min_deal_size: Optional[float],
    min_quality_score: Optional[int],
) -> None:
    """Helper function to validate deal size and quality score query parameters at the API boundary."""
    if min_deal_size is not None and min_deal_size < 0:
        logger.warning("Invalid min_deal_size parameter: %s", min_deal_size)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_INPUT",
                "message": "min_deal_size must be greater than or equal to 0",
                "details": {"min_deal_size": min_deal_size},
            },
        )
    if min_quality_score is not None and (min_quality_score < 0 or min_quality_score > 100):
        logger.warning("Invalid min_quality_score parameter: %s", min_quality_score)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_INPUT",
                "message": "min_quality_score must be between 0 and 100",
                "details": {"min_quality_score": min_quality_score},
            },
        )


# --- Endpoints ---


@router.get(
    "/heatmap",
    response_model=List[TerritoryDensityMetricsSchema],
    summary="Get territory opportunity density metrics",
    description="Retrieve regional opportunity density, deal values, and coordinates for geographic heat maps.",
)
def get_territory_heatmap_endpoint(
    region_name: Optional[str] = Query(
        None, description="Filter metrics by specific geographic region name"
    ),
    min_deal_size: Optional[float] = Query(
        None, ge=0.0, description="Filter by minimum deal value threshold"
    ),
    min_quality_score: Optional[int] = Query(
        None, ge=0, le=100, description="Filter by minimum lead quality score (0 to 100)"
    ),
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter leads created on or after this UTC timestamp"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter leads created on or before this UTC timestamp"
    ),
    source_id: Optional[str] = Query(
        None, description="Filter metrics by specific lead acquisition source ID"
    ),
    db: DBSession = Depends(get_db),
    current_session: SessionModel = Depends(get_current_session),
) -> List[TerritoryDensityMetricsSchema]:
    """Retrieve opportunity density and financial metrics across geographic territory regions.

    Enforces input validation and session authentication. Supports filtering by region,
    deal size, lead quality score, creation date range, and lead source ID.
    """
    _validate_date_range(start_timestamp, end_timestamp)
    _validate_numeric_filters(min_deal_size, min_quality_score)

    metrics = get_territory_density_metrics(
        db=db,
        region_name=region_name,
        min_deal_size=min_deal_size,
        min_quality_score=min_quality_score,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_id=source_id,
    )
    return [TerritoryDensityMetricsSchema.model_validate(m.to_dict()) for m in metrics]


@router.get(
    "/unmapped",
    response_model=TerritoryDensityMetricsSchema,
    summary="Get unmapped territory density metrics",
    description="Retrieve opportunity density metrics specifically for leads lacking mapped geographic region attributes.",
)
def get_unmapped_territory_endpoint(
    min_deal_size: Optional[float] = Query(
        None, ge=0.0, description="Filter by minimum deal value threshold"
    ),
    min_quality_score: Optional[int] = Query(
        None, ge=0, le=100, description="Filter by minimum lead quality score (0 to 100)"
    ),
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter leads created on or after this UTC timestamp"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter leads created on or before this UTC timestamp"
    ),
    db: DBSession = Depends(get_db),
    current_session: SessionModel = Depends(get_current_session),
) -> TerritoryDensityMetricsSchema:
    """Retrieve opportunity density metrics specifically for the unmapped region category.

    Enforces input validation and session authentication. Supports filtering by deal size,
    lead quality score, and creation date range.
    """
    _validate_date_range(start_timestamp, end_timestamp)
    _validate_numeric_filters(min_deal_size, min_quality_score)

    metric = get_unmapped_region_metrics(
        db=db,
        min_deal_size=min_deal_size,
        min_quality_score=min_quality_score,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    if metric is None:
        return TerritoryDensityMetricsSchema(
            region_name=settings.DEFAULT_UNMAPPED_REGION_NAME,
            opportunity_count=0,
            total_deal_value=0.0,
            average_quality_score=0.0,
            density_score=0.0,
            latitude=0.0,
            longitude=0.0,
        )
    return TerritoryDensityMetricsSchema.model_validate(metric.to_dict())
