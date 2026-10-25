"""Domain service for regional opportunity density aggregation and unmapped territory handling."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.lead import Lead

logger = logging.getLogger(__name__)

# Standard geographic coordinates mapping for common territory regions
REGION_COORDINATES: Dict[str, Dict[str, float]] = {
    "North America": {"latitude": 37.0902, "longitude": -95.7129},
    "Europe": {"latitude": 54.5260, "longitude": 15.2551},
    "EMEA": {"latitude": 50.1109, "longitude": 8.6821},
    "APAC": {"latitude": 1.3521, "longitude": 103.8198},
    "Asia Pacific": {"latitude": 1.3521, "longitude": 103.8198},
    "Latin America": {"latitude": -14.2350, "longitude": -51.9253},
    "LATAM": {"latitude": -14.2350, "longitude": -51.9253},
    "Middle East": {"latitude": 29.2985, "longitude": 42.5510},
    "Unmapped Region": {"latitude": 0.0, "longitude": 0.0},
}


def normalize_region_name(region_name: Optional[str]) -> str:
    """Normalize region name string, falling back to configured default for missing values.

    Args:
        region_name: Raw region name string or None.

    Returns:
        Cleaned region name string or DEFAULT_UNMAPPED_REGION_NAME.
    """
    if not region_name or not region_name.strip():
        return settings.DEFAULT_UNMAPPED_REGION_NAME
    return region_name.strip()


def calculate_density_score(
    opportunity_count: int,
    total_opportunities: int,
) -> float:
    """Calculate opportunity density score as percentage of total opportunity volume.

    Args:
        opportunity_count: Number of opportunities in specific territory region.
        total_opportunities: Total opportunity count across all filtered regions.

    Returns:
        Density score percentage rounded to 2 decimal places (0.0 to 100.0).
    """
    if total_opportunities <= 0 or opportunity_count <= 0:
        return 0.0

    density = (opportunity_count / total_opportunities) * 100.0
    return round(density, 2)


def get_region_coordinates(region_name: str) -> Dict[str, float]:
    """Retrieve latitude and longitude coordinates for a named geographic territory.

    Args:
        region_name: Territory region identifier string.

    Returns:
        Dictionary containing 'latitude' and 'longitude' float values.
    """
    clean_name = normalize_region_name(region_name)
    coords = REGION_COORDINATES.get(clean_name)
    if coords:
        return dict(coords)
    return {"latitude": 0.0, "longitude": 0.0}


@dataclass
class TerritoryDensityMetrics:
    """Aggregated opportunity density and financial metrics for a geographic territory."""

    region_name: str
    opportunity_count: int
    total_deal_value: float
    average_quality_score: float
    density_score: float
    latitude: float
    longitude: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics instance to dictionary representation."""
        return asdict(self)


def get_territory_density_metrics(
    db: Session,
    region_name: Optional[str] = None,
    min_deal_size: Optional[float | Decimal | int] = None,
    min_quality_score: Optional[int] = None,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
    source_id: Optional[str] = None,
) -> List[TerritoryDensityMetrics]:
    """Aggregate opportunity density and deal metrics across geographic regions.

    Applies filtering by region name, minimum deal size, minimum lead quality score,
    date range, and lead source ID. Groups missing or empty geographic records into
    the configured unmapped region category.

    Args:
        db: Synchronous SQLAlchemy database session.
        region_name: Optional geographic region filter.
        min_deal_size: Optional minimum deal value threshold filter.
        min_quality_score: Optional minimum lead quality score threshold filter.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.
        source_id: Optional lead acquisition source ID filter.

    Returns:
        List of TerritoryDensityMetrics ordered by opportunity count descending.
    """
    stmt = select(Lead)

    # Apply region filter if provided
    if region_name and region_name.strip():
        clean_filter = region_name.strip()
        if clean_filter.lower() == settings.DEFAULT_UNMAPPED_REGION_NAME.lower():
            stmt = stmt.where(
                or_(
                    Lead.region_name == settings.DEFAULT_UNMAPPED_REGION_NAME,
                    Lead.region_name.is_(None),
                    Lead.region_name == "",
                )
            )
        else:
            stmt = stmt.where(Lead.region_name == clean_filter)

    # Apply deal size filter
    if min_deal_size is not None and float(min_deal_size) > 0:
        stmt = stmt.where(Lead.deal_value >= Decimal(str(min_deal_size)))

    # Apply quality score filter
    if min_quality_score is not None and int(min_quality_score) > 0:
        stmt = stmt.where(Lead.quality_score >= int(min_quality_score))

    # Apply timestamp filters
    if start_timestamp is not None:
        stmt = stmt.where(Lead.created_timestamp >= start_timestamp)
    if end_timestamp is not None:
        stmt = stmt.where(Lead.created_timestamp <= end_timestamp)

    # Apply source filter
    if source_id and source_id.strip():
        stmt = stmt.where(Lead.source_id == source_id.strip())

    leads: List[Lead] = list(db.scalars(stmt).all())
    total_matching_leads = len(leads)

    if total_matching_leads == 0:
        return []

    # Group leads by normalized region name
    region_groups: Dict[str, List[Lead]] = {}
    for lead in leads:
        norm_name = normalize_region_name(lead.region_name)
        if norm_name not in region_groups:
            region_groups[norm_name] = []
        region_groups[norm_name].append(lead)

    metrics_list: List[TerritoryDensityMetrics] = []
    for reg_name, group_leads in region_groups.items():
        opp_count = len(group_leads)
        total_val = sum(float(l.deal_value or 0.0) for l in group_leads)
        avg_quality = (
            sum(l.quality_score for l in group_leads) / opp_count if opp_count > 0 else 0.0
        )
        density = calculate_density_score(opp_count, total_matching_leads)
        coords = get_region_coordinates(reg_name)

        metrics_list.append(
            TerritoryDensityMetrics(
                region_name=reg_name,
                opportunity_count=opp_count,
                total_deal_value=round(total_val, 2),
                average_quality_score=round(avg_quality, 2),
                density_score=density,
                latitude=coords["latitude"],
                longitude=coords["longitude"],
            )
        )

    # Sort by opportunity count descending, then total deal value descending, then region_name
    metrics_list.sort(
        key=lambda m: (-m.opportunity_count, -m.total_deal_value, m.region_name)
    )
    return metrics_list


def get_unmapped_region_metrics(
    db: Session,
    min_deal_size: Optional[float | Decimal | int] = None,
    min_quality_score: Optional[int] = None,
    start_timestamp: Optional[datetime] = None,
    end_timestamp: Optional[datetime] = None,
) -> Optional[TerritoryDensityMetrics]:
    """Retrieve density metrics specifically for the unmapped region category.

    Args:
        db: Synchronous SQLAlchemy database session.
        min_deal_size: Optional minimum deal value threshold filter.
        min_quality_score: Optional minimum lead quality score threshold filter.
        start_timestamp: Optional filter start timestamp for lead creation.
        end_timestamp: Optional filter end timestamp for lead creation.

    Returns:
        TerritoryDensityMetrics instance for unmapped region if leads exist, else None.
    """
    metrics = get_territory_density_metrics(
        db=db,
        region_name=settings.DEFAULT_UNMAPPED_REGION_NAME,
        min_deal_size=min_deal_size,
        min_quality_score=min_quality_score,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    return metrics[0] if metrics else None
