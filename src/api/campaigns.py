"""FastAPI endpoint router and Pydantic validation schemas for side-by-side campaign comparison matrix analytics."""

import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.models.base import get_db
from src.models.campaign import Campaign
from src.models.session import Session as SessionModel
from src.services.campaigns.comparison_service import compare_campaigns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


# --- Schemas ---


class CampaignSchema(BaseModel):
    """Schema for individual marketing campaign metadata."""

    campaign_id: str = Field(..., description="Unique campaign UUID identifier")
    campaign_name: str = Field(..., description="Display name of the marketing campaign")
    budget_amount: float = Field(..., ge=0.0, description="Allocated budget amount for the campaign")
    spent_amount: float = Field(..., ge=0.0, description="Total spend incurred for the campaign")
    start_timestamp: Optional[datetime] = Field(None, description="Campaign start timestamp")
    end_timestamp: Optional[datetime] = Field(None, description="Campaign end timestamp")
    created_timestamp: datetime = Field(..., description="Record creation timestamp")

    model_config = ConfigDict(from_attributes=True)


class CampaignComparisonItemSchema(BaseModel):
    """Schema for performance metrics of a single campaign within a comparison matrix."""

    campaign_id: str = Field(..., description="Unique campaign UUID identifier")
    campaign_name: str = Field(..., description="Display name of the marketing campaign")
    budget_amount: float = Field(..., ge=0.0, description="Allocated budget amount for the campaign")
    spent_amount: float = Field(..., ge=0.0, description="Total spend incurred for the campaign")
    leads_generated: int = Field(..., ge=0, description="Total number of leads acquired through this campaign")
    average_quality_score: float = Field(
        ..., ge=0.0, le=100.0, description="Average lead quality score (0 to 100)"
    )
    converted_leads: int = Field(..., ge=0, description="Total number of converted/won leads from this campaign")
    overall_conversion_rate: float = Field(
        ..., ge=0.0, le=100.0, description="Lead-to-conversion rate percentage"
    )
    attributed_revenue: float = Field(
        ..., ge=0.0, description="Total attributed deal revenue from campaign leads"
    )
    total_roi_percentage: float = Field(
        ..., description="Return on Investment (ROI) percentage"
    )
    cost_per_lead: float = Field(..., ge=0.0, description="Cost per lead acquired")
    cost_per_conversion: float = Field(..., ge=0.0, description="Cost per converted lead")
    start_timestamp: Optional[datetime] = Field(None, description="Campaign start timestamp")
    end_timestamp: Optional[datetime] = Field(None, description="Campaign end timestamp")

    model_config = ConfigDict(from_attributes=True)


class CampaignComparisonSummarySchema(BaseModel):
    """Schema for aggregate summary metrics across compared marketing campaigns."""

    total_campaigns_compared: int = Field(
        ..., ge=0, description="Number of campaigns included in the comparison matrix"
    )
    best_roi_campaign_id: Optional[str] = Field(
        None, description="Campaign ID with the highest positive ROI percentage"
    )
    best_roi_campaign_name: Optional[str] = Field(
        None, description="Campaign name with the highest positive ROI percentage"
    )
    best_conversion_campaign_id: Optional[str] = Field(
        None, description="Campaign ID with the highest conversion rate"
    )
    best_conversion_campaign_name: Optional[str] = Field(
        None, description="Campaign name with the highest conversion rate"
    )
    highest_lead_volume_campaign_id: Optional[str] = Field(
        None, description="Campaign ID with the highest lead volume"
    )
    highest_lead_volume_campaign_name: Optional[str] = Field(
        None, description="Campaign name with the highest lead volume"
    )
    total_spend_across_campaigns: float = Field(
        ..., ge=0.0, description="Aggregate total spend across compared campaigns"
    )
    total_revenue_across_campaigns: float = Field(
        ..., ge=0.0, description="Aggregate total revenue generated across compared campaigns"
    )
    total_leads_across_campaigns: int = Field(
        ..., ge=0, description="Aggregate total leads generated across compared campaigns"
    )
    average_roi_percentage: float = Field(
        ..., description="Average ROI percentage across compared campaigns"
    )

    model_config = ConfigDict(from_attributes=True)


class CampaignComparisonMatrixSchema(BaseModel):
    """Schema for side-by-side marketing campaign comparison matrix response."""

    campaigns: List[CampaignComparisonItemSchema] = Field(
        default_factory=list, description="List of performance metric items for compared campaigns"
    )
    summary: CampaignComparisonSummarySchema = Field(
        ..., description="Comparative summary metrics highlighting top performers"
    )

    model_config = ConfigDict(from_attributes=True)


# --- Validation Helpers ---


def _validate_date_range(
    start_timestamp: Optional[datetime],
    end_timestamp: Optional[datetime],
) -> None:
    """Validate date/time range query parameters at the API boundary (FR-12, NFR-3)."""
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


def _parse_campaign_ids(campaign_ids_raw: Optional[List[str]]) -> Optional[List[str]]:
    """Parse and sanitize campaign_ids query parameters supporting both multi-query and comma-separated lists."""
    if not campaign_ids_raw:
        return None

    parsed_ids: List[str] = []
    for raw in campaign_ids_raw:
        if not raw:
            continue
        # Split on comma if present
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        parsed_ids.extend(parts)

    return parsed_ids if parsed_ids else None


# --- Endpoints ---


@router.get(
    "/compare",
    response_model=CampaignComparisonMatrixSchema,
    summary="Get campaign performance comparison matrix",
    description=(
        "Returns side-by-side evaluation metrics comparing performance metrics for selected marketing campaigns, "
        "including spend, leads generated, quality score, conversion rate, attributed revenue, and ROI."
    ),
)
def get_campaign_comparison_matrix(
    campaign_ids: Optional[List[str]] = Query(
        None,
        description="Optional list of campaign UUIDs to compare (supports repeated query parameter or comma-separated list)",
    ),
    start_timestamp: Optional[datetime] = Query(
        None,
        description="Optional start timestamp filter for lead creation date window",
    ),
    end_timestamp: Optional[datetime] = Query(
        None,
        description="Optional end timestamp filter for lead creation date window",
    ),
    db: DBSession = Depends(get_db),
    current_session: SessionModel = Depends(get_current_session),
) -> CampaignComparisonMatrixSchema:
    """Retrieve side-by-side marketing campaign comparison matrix (FR-10, FR-11, FR-12, NFR-3, NFR-5)."""
    _validate_date_range(start_timestamp, end_timestamp)
    parsed_ids = _parse_campaign_ids(campaign_ids)

    logger.info(
        "User %s comparing campaigns with filters: campaign_ids=%s, start_timestamp=%s, end_timestamp=%s",
        current_session.user_id,
        parsed_ids,
        start_timestamp,
        end_timestamp,
    )

    matrix = compare_campaigns(
        db=db,
        campaign_ids=parsed_ids,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )

    return CampaignComparisonMatrixSchema(
        campaigns=[
            CampaignComparisonItemSchema(
                campaign_id=item.campaign_id,
                campaign_name=item.campaign_name,
                budget_amount=item.budget_amount,
                spent_amount=item.spent_amount,
                leads_generated=item.leads_generated,
                average_quality_score=item.average_quality_score,
                converted_leads=item.converted_leads,
                overall_conversion_rate=item.overall_conversion_rate,
                attributed_revenue=item.attributed_revenue,
                total_roi_percentage=item.total_roi_percentage,
                cost_per_lead=item.cost_per_lead,
                cost_per_conversion=item.cost_per_conversion,
                start_timestamp=item.start_timestamp,
                end_timestamp=item.end_timestamp,
            )
            for item in matrix.campaigns
        ],
        summary=CampaignComparisonSummarySchema(
            total_campaigns_compared=matrix.summary.total_campaigns_compared,
            best_roi_campaign_id=matrix.summary.best_roi_campaign_id,
            best_roi_campaign_name=matrix.summary.best_roi_campaign_name,
            best_conversion_campaign_id=matrix.summary.best_conversion_campaign_id,
            best_conversion_campaign_name=matrix.summary.best_conversion_campaign_name,
            highest_lead_volume_campaign_id=matrix.summary.highest_lead_volume_campaign_id,
            highest_lead_volume_campaign_name=matrix.summary.highest_lead_volume_campaign_name,
            total_spend_across_campaigns=matrix.summary.total_spend_across_campaigns,
            total_revenue_across_campaigns=matrix.summary.total_revenue_across_campaigns,
            total_leads_across_campaigns=matrix.summary.total_leads_across_campaigns,
            average_roi_percentage=matrix.summary.average_roi_percentage,
        ),
    )


@router.get(
    "",
    response_model=List[CampaignSchema],
    summary="List all marketing campaigns",
    description="Returns a list of all recorded marketing campaigns ordered by campaign name.",
)
@router.get(
    "/",
    response_model=List[CampaignSchema],
    include_in_schema=False,
)
def list_campaigns(
    db: DBSession = Depends(get_db),
    current_session: SessionModel = Depends(get_current_session),
) -> List[CampaignSchema]:
    """Retrieve all recorded marketing campaign entities (FR-10, NFR-5)."""
    stmt = select(Campaign).order_by(Campaign.campaign_name)
    campaigns = db.scalars(stmt).all()
    return [
        CampaignSchema(
            campaign_id=c.campaign_id,
            campaign_name=c.campaign_name,
            budget_amount=float(c.budget_amount) if c.budget_amount is not None else 0.0,
            spent_amount=float(c.spent_amount) if c.spent_amount is not None else 0.0,
            start_timestamp=c.start_timestamp,
            end_timestamp=c.end_timestamp,
            created_timestamp=c.created_timestamp,
        )
        for c in campaigns
    ]


@router.get(
    "/{campaign_id}",
    response_model=CampaignSchema,
    summary="Get single marketing campaign details",
    description="Returns details for a specific marketing campaign by campaign_id UUID.",
)
def get_campaign_detail(
    campaign_id: str,
    db: DBSession = Depends(get_db),
    current_session: SessionModel = Depends(get_current_session),
) -> CampaignSchema:
    """Retrieve details for a single marketing campaign (FR-10, NFR-5)."""
    stmt = select(Campaign).where(Campaign.campaign_id == campaign_id)
    campaign = db.scalars(stmt).first()
    if not campaign:
        logger.warning("Campaign not found with ID: %s", campaign_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CAMPAIGN_NOT_FOUND",
                "message": f"Campaign with ID '{campaign_id}' was not found.",
                "details": {"campaign_id": campaign_id},
            },
        )

    return CampaignSchema(
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.campaign_name,
        budget_amount=float(campaign.budget_amount) if campaign.budget_amount is not None else 0.0,
        spent_amount=float(campaign.spent_amount) if campaign.spent_amount is not None else 0.0,
        start_timestamp=campaign.start_timestamp,
        end_timestamp=campaign.end_timestamp,
        created_timestamp=campaign.created_timestamp,
    )
