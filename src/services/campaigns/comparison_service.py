"""Domain service for side-by-side marketing campaign comparison and performance matrix calculation."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import logging
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.campaign import Campaign
from src.models.lead import Lead
from src.services.analytics.roi_service import calculate_roi_percentage

logger = logging.getLogger(__name__)


@dataclass
class CampaignComparisonItem:
    """Performance metrics for a single campaign within a comparison matrix."""

    campaign_id: str
    campaign_name: str
    budget_amount: float
    spent_amount: float
    leads_generated: int
    average_quality_score: float
    converted_leads: int
    overall_conversion_rate: float
    attributed_revenue: float
    total_roi_percentage: float
    cost_per_lead: float
    cost_per_conversion: float
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert campaign comparison item to a dictionary representation."""
        data = asdict(self)
        if isinstance(self.start_timestamp, datetime):
            data["start_timestamp"] = self.start_timestamp.isoformat()
        if isinstance(self.end_timestamp, datetime):
            data["end_timestamp"] = self.end_timestamp.isoformat()
        return data


@dataclass
class CampaignComparisonSummary:
    """Summary metrics highlighting top performers and totals across compared campaigns."""

    total_campaigns_compared: int
    best_roi_campaign_id: Optional[str]
    best_roi_campaign_name: Optional[str]
    best_conversion_campaign_id: Optional[str]
    best_conversion_campaign_name: Optional[str]
    highest_lead_volume_campaign_id: Optional[str]
    highest_lead_volume_campaign_name: Optional[str]
    total_spend_across_campaigns: float
    total_revenue_across_campaigns: float
    total_leads_across_campaigns: int
    average_roi_percentage: float

    def to_dict(self) -> dict[str, Any]:
        """Convert summary instance to a dictionary representation."""
        return asdict(self)


@dataclass
class CampaignComparisonMatrix:
    """Side-by-side evaluation matrix containing items for each campaign and summary statistics."""

    campaigns: List[CampaignComparisonItem]
    summary: CampaignComparisonSummary

    def to_dict(self) -> dict[str, Any]:
        """Convert matrix instance to a dictionary representation."""
        return {
            "campaigns": [item.to_dict() for item in self.campaigns],
            "summary": self.summary.to_dict(),
        }


def calculate_campaign_item_metrics(
    campaign: Campaign,
    leads: List[Lead],
) -> CampaignComparisonItem:
    """Calculate performance metrics for a single campaign given its lead records.

    Args:
        campaign: Campaign ORM model instance.
        leads: List of Lead ORM model instances attributed to this campaign.

    Returns:
        CampaignComparisonItem instance containing aggregated performance metrics.
    """
    leads_generated = len(leads)
    budget_val = float(campaign.budget_amount) if campaign.budget_amount is not None else 0.0
    spent_val = float(campaign.spent_amount) if campaign.spent_amount is not None else 0.0

    if leads_generated > 0:
        avg_quality = round(
            sum(lead.quality_score for lead in leads) / leads_generated, 2
        )
        attributed_revenue = float(sum(lead.deal_value for lead in leads))
        converted_leads = sum(
            1
            for lead in leads
            if lead.deal_value > Decimal("0.00")
            or lead.status_stage in ("CLOSED_WON", "CONVERTED")
        )
        overall_conversion_rate = round(
            (converted_leads / leads_generated) * 100.0, 2
        )
        cost_per_lead = round(spent_val / leads_generated, 2)
    else:
        avg_quality = 0.0
        attributed_revenue = 0.0
        converted_leads = 0
        overall_conversion_rate = 0.0
        cost_per_lead = 0.0

    cost_per_conversion = (
        round(spent_val / converted_leads, 2) if converted_leads > 0 else 0.0
    )
    total_roi = calculate_roi_percentage(attributed_revenue, spent_val)

    return CampaignComparisonItem(
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.campaign_name,
        budget_amount=round(budget_val, 2),
        spent_amount=round(spent_val, 2),
        leads_generated=leads_generated,
        average_quality_score=avg_quality,
        converted_leads=converted_leads,
        overall_conversion_rate=overall_conversion_rate,
        attributed_revenue=round(attributed_revenue, 2),
        total_roi_percentage=total_roi,
        cost_per_lead=cost_per_lead,
        cost_per_conversion=cost_per_conversion,
        start_timestamp=campaign.start_timestamp,
        end_timestamp=campaign.end_timestamp,
    )


def compare_campaigns(
    db: Session,
    campaign_ids: Optional[List[str]] = None,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
) -> CampaignComparisonMatrix:
    """Generate a side-by-side performance comparison matrix across selected marketing campaigns.

    Args:
        db: Synchronous SQLAlchemy database session.
        campaign_ids: Optional list of campaign UUID strings to compare. If None, queries all.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.

    Returns:
        CampaignComparisonMatrix object with per-campaign metrics and comparative summary statistics.
    """
    if campaign_ids is not None and len(campaign_ids) == 0:
        empty_summary = CampaignComparisonSummary(
            total_campaigns_compared=0,
            best_roi_campaign_id=None,
            best_roi_campaign_name=None,
            best_conversion_campaign_id=None,
            best_conversion_campaign_name=None,
            highest_lead_volume_campaign_id=None,
            highest_lead_volume_campaign_name=None,
            total_spend_across_campaigns=0.0,
            total_revenue_across_campaigns=0.0,
            total_leads_across_campaigns=0,
            average_roi_percentage=0.0,
        )
        return CampaignComparisonMatrix(campaigns=[], summary=empty_summary)

    stmt = select(Campaign)
    if campaign_ids:
        stmt = stmt.where(Campaign.campaign_id.in_(campaign_ids))
    stmt = stmt.order_by(Campaign.campaign_name)

    campaigns = db.scalars(stmt).all()

    items: List[CampaignComparisonItem] = []
    for campaign in campaigns:
        lead_stmt = select(Lead).where(Lead.campaign_id == campaign.campaign_id)
        if start_timestamp:
            lead_stmt = lead_stmt.where(Lead.created_timestamp >= start_timestamp)
        if end_timestamp:
            lead_stmt = lead_stmt.where(Lead.created_timestamp <= end_timestamp)

        leads = db.scalars(lead_stmt).all()
        item = calculate_campaign_item_metrics(campaign, leads)
        items.append(item)

    if not items:
        summary = CampaignComparisonSummary(
            total_campaigns_compared=0,
            best_roi_campaign_id=None,
            best_roi_campaign_name=None,
            best_conversion_campaign_id=None,
            best_conversion_campaign_name=None,
            highest_lead_volume_campaign_id=None,
            highest_lead_volume_campaign_name=None,
            total_spend_across_campaigns=0.0,
            total_revenue_across_campaigns=0.0,
            total_leads_across_campaigns=0,
            average_roi_percentage=0.0,
        )
        return CampaignComparisonMatrix(campaigns=[], summary=summary)

    best_roi_item = max(items, key=lambda x: x.total_roi_percentage)
    best_conv_item = max(items, key=lambda x: x.overall_conversion_rate)
    highest_leads_item = max(items, key=lambda x: x.leads_generated)

    total_spend = sum(item.spent_amount for item in items)
    total_revenue = sum(item.attributed_revenue for item in items)
    total_leads = sum(item.leads_generated for item in items)
    avg_roi = round(sum(item.total_roi_percentage for item in items) / len(items), 2)

    summary = CampaignComparisonSummary(
        total_campaigns_compared=len(items),
        best_roi_campaign_id=best_roi_item.campaign_id if best_roi_item.total_roi_percentage > 0 else None,
        best_roi_campaign_name=best_roi_item.campaign_name if best_roi_item.total_roi_percentage > 0 else None,
        best_conversion_campaign_id=best_conv_item.campaign_id if best_conv_item.overall_conversion_rate > 0 else None,
        best_conversion_campaign_name=best_conv_item.campaign_name if best_conv_item.overall_conversion_rate > 0 else None,
        highest_lead_volume_campaign_id=highest_leads_item.campaign_id if highest_leads_item.leads_generated > 0 else None,
        highest_lead_volume_campaign_name=highest_leads_item.campaign_name if highest_leads_item.leads_generated > 0 else None,
        total_spend_across_campaigns=round(total_spend, 2),
        total_revenue_across_campaigns=round(total_revenue, 2),
        total_leads_across_campaigns=total_leads,
        average_roi_percentage=avg_roi,
    )

    return CampaignComparisonMatrix(campaigns=items, summary=summary)
