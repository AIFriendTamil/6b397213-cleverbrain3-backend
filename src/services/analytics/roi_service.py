"""Domain service for ROI calculation formulas, lead source analytics, and zero-revenue edge cases."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import logging
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.campaign import Campaign
from src.models.lead import Lead, LeadSource

logger = logging.getLogger(__name__)


def calculate_roi_percentage(
    attributed_revenue: Decimal | float | int,
    cost_amount: Decimal | float | int,
) -> float:
    """Calculate the Return on Investment (ROI) percentage for a lead source or campaign.

    Formula:
        ROI (%) = ((Attributed Revenue - Source Cost) / Source Cost) * 100

    Edge Case / Zero Handling Rules:
        - If Source Cost <= 0 or Attributed Revenue <= 0, return 0.0%.
        - Prevents division-by-zero errors or invalid negative ROI values for zero-revenue scenarios.

    Args:
        attributed_revenue: Sum of deal values generated from converted leads.
        cost_amount: Total expense or budget spent for the source or campaign.

    Returns:
        Calculated ROI as a rounded floating point percentage value.
    """
    revenue_val = float(attributed_revenue) if attributed_revenue is not None else 0.0
    cost_val = float(cost_amount) if cost_amount is not None else 0.0

    if cost_val <= 0.0 or revenue_val <= 0.0:
        return 0.0

    try:
        roi = ((revenue_val - cost_val) / cost_val) * 100.0
        return round(roi, 2)
    except ZeroDivisionError:
        return 0.0


@dataclass
class SourceROIMetrics:
    """Aggregated ROI and quality metrics for a lead acquisition source."""

    source_id: str
    source_name: str
    total_leads: int
    average_quality_score: float
    total_spend: float
    attributed_revenue: float
    calculated_roi_percentage: float

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics instance to dictionary representation."""
        return asdict(self)


@dataclass
class CampaignROIMetrics:
    """Aggregated performance metrics and ROI for a campaign."""

    campaign_id: str
    campaign_name: str
    leads_generated: int
    average_quality_score: float
    overall_conversion_rate: float
    total_spend: float
    attributed_revenue: float
    total_roi_percentage: float

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics instance to dictionary representation."""
        return asdict(self)


def get_lead_source_roi_metrics(
    db: Session,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
    source_id: Optional[str] = None,
    region_name: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> List[SourceROIMetrics]:
    """Retrieve aggregated performance metrics and ROI for lead acquisition sources.

    Args:
        db: Synchronous SQLAlchemy database session.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.
        source_id: Optional UUID string filter for a specific lead source.
        region_name: Optional geographic region filter for lead creation.
        campaign_id: Optional campaign UUID filter for lead creation.

    Returns:
        List of SourceROIMetrics objects ordered by source name.
    """
    stmt = select(LeadSource)
    if source_id:
        stmt = stmt.where(LeadSource.source_id == source_id)
    stmt = stmt.order_by(LeadSource.source_name)

    sources = db.scalars(stmt).all()
    results: List[SourceROIMetrics] = []

    for source in sources:
        lead_stmt = select(Lead).where(Lead.source_id == source.source_id)
        if start_timestamp:
            lead_stmt = lead_stmt.where(Lead.created_timestamp >= start_timestamp)
        if end_timestamp:
            lead_stmt = lead_stmt.where(Lead.created_timestamp <= end_timestamp)
        if region_name:
            lead_stmt = lead_stmt.where(Lead.region_name == region_name)
        if campaign_id:
            lead_stmt = lead_stmt.where(Lead.campaign_id == campaign_id)

        leads = db.scalars(lead_stmt).all()
        total_leads = len(leads)

        if total_leads > 0:
            avg_quality = round(
                sum(lead.quality_score for lead in leads) / total_leads, 2
            )
            attributed_revenue = float(sum(lead.deal_value for lead in leads))
        else:
            avg_quality = 0.0
            attributed_revenue = 0.0

        total_spend = float(source.cost_amount) if source.cost_amount is not None else 0.0
        calculated_roi = calculate_roi_percentage(attributed_revenue, total_spend)

        results.append(
            SourceROIMetrics(
                source_id=source.source_id,
                source_name=source.source_name,
                total_leads=total_leads,
                average_quality_score=avg_quality,
                total_spend=round(total_spend, 2),
                attributed_revenue=round(attributed_revenue, 2),
                calculated_roi_percentage=calculated_roi,
            )
        )

    return results


def get_acquisition_summary(
    db: Session,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
    region_name: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve overall aggregate acquisition summary and ROI metrics across all sources.

    Args:
        db: Synchronous SQLAlchemy database session.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.
        region_name: Optional geographic region filter for lead creation.
        campaign_id: Optional campaign UUID filter for lead creation.

    Returns:
        Dictionary containing summary statistics and individual source metrics list.
    """
    sources_metrics = get_lead_source_roi_metrics(
        db,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        region_name=region_name,
        campaign_id=campaign_id,
    )
    total_sources = len(sources_metrics)
    total_leads = sum(s.total_leads for s in sources_metrics)
    if total_leads > 0:
        overall_avg_quality = round(
            sum(s.average_quality_score * s.total_leads for s in sources_metrics) / total_leads, 2
        )
    else:
        overall_avg_quality = 0.0
    total_spend = round(sum(s.total_spend for s in sources_metrics), 2)
    total_attributed_revenue = round(sum(s.attributed_revenue for s in sources_metrics), 2)
    overall_roi = calculate_roi_percentage(total_attributed_revenue, total_spend)

    return {
        "total_sources": total_sources,
        "total_leads": total_leads,
        "overall_average_quality_score": overall_avg_quality,
        "total_spend": total_spend,
        "total_attributed_revenue": total_attributed_revenue,
        "overall_roi_percentage": overall_roi,
        "sources": sources_metrics,
    }


def get_campaign_roi_metrics(
    db: Session,
    campaign_ids: Optional[List[str]] = None,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
) -> List[CampaignROIMetrics]:
    """Retrieve aggregated performance matrix and ROI for marketing campaigns.

    Args:
        db: Synchronous SQLAlchemy database session.
        campaign_ids: Optional list of campaign UUID strings to compare.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.

    Returns:
        List of CampaignROIMetrics objects ordered by campaign name.
    """
    stmt = select(Campaign)
    if campaign_ids:
        stmt = stmt.where(Campaign.campaign_id.in_(campaign_ids))
    stmt = stmt.order_by(Campaign.campaign_name)

    campaigns = db.scalars(stmt).all()
    results: List[CampaignROIMetrics] = []

    for campaign in campaigns:
        lead_stmt = select(Lead).where(Lead.campaign_id == campaign.campaign_id)
        if start_timestamp:
            lead_stmt = lead_stmt.where(Lead.created_timestamp >= start_timestamp)
        if end_timestamp:
            lead_stmt = lead_stmt.where(Lead.created_timestamp <= end_timestamp)

        leads = db.scalars(lead_stmt).all()
        leads_generated = len(leads)

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
        else:
            avg_quality = 0.0
            attributed_revenue = 0.0
            overall_conversion_rate = 0.0

        total_spend = float(campaign.spent_amount) if campaign.spent_amount is not None else 0.0
        total_roi = calculate_roi_percentage(attributed_revenue, total_spend)

        results.append(
            CampaignROIMetrics(
                campaign_id=campaign.campaign_id,
                campaign_name=campaign.campaign_name,
                leads_generated=leads_generated,
                average_quality_score=avg_quality,
                overall_conversion_rate=overall_conversion_rate,
                total_spend=round(total_spend, 2),
                attributed_revenue=round(attributed_revenue, 2),
                total_roi_percentage=total_roi,
            )
        )

    return results
