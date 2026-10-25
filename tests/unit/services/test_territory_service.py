"""Unit tests for regional opportunity density aggregation and unmapped region handling."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.base import Base, create_db_engine
from src.models.lead import Lead, LeadSource
from src.services.analytics.territory_service import (
    TerritoryDensityMetrics,
    calculate_density_score,
    get_region_coordinates,
    get_territory_density_metrics,
    get_unmapped_region_metrics,
    normalize_region_name,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with initialized tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_normalize_region_name():
    """Verify region name normalization for valid strings, whitespace, empty, and None inputs."""
    assert normalize_region_name("North America") == "North America"
    assert normalize_region_name("  Europe  ") == "Europe"
    assert normalize_region_name("") == settings.DEFAULT_UNMAPPED_REGION_NAME
    assert normalize_region_name("   ") == settings.DEFAULT_UNMAPPED_REGION_NAME
    assert normalize_region_name(None) == settings.DEFAULT_UNMAPPED_REGION_NAME


def test_calculate_density_score():
    """Verify opportunity density percentage calculations and zero handling."""
    assert calculate_density_score(5, 20) == 25.0
    assert calculate_density_score(1, 3) == 33.33
    assert calculate_density_score(0, 10) == 0.0
    assert calculate_density_score(5, 0) == 0.0
    assert calculate_density_score(-1, 10) == 0.0


def test_get_region_coordinates():
    """Verify coordinate lookup for known regions, unmapped regions, and unknown inputs."""
    na_coords = get_region_coordinates("North America")
    assert na_coords["latitude"] == 37.0902
    assert na_coords["longitude"] == -95.7129

    unmapped_coords = get_region_coordinates("Unmapped Region")
    assert unmapped_coords["latitude"] == 0.0
    assert unmapped_coords["longitude"] == 0.0

    unknown_coords = get_region_coordinates("Unknown Territory XYZ")
    assert unknown_coords["latitude"] == 0.0
    assert unknown_coords["longitude"] == 0.0


def test_get_territory_density_metrics_empty_db(test_session: Session):
    """Verify empty database returns an empty metrics list."""
    metrics = get_territory_density_metrics(test_session)
    assert metrics == []


def test_get_territory_density_metrics_aggregation(test_session: Session):
    """Verify regional aggregation, unmapped grouping, quality score averages, and deal totals."""
    source = LeadSource(source_name="Inbound Web")
    test_session.add(source)
    test_session.commit()

    # Leads for North America
    l1 = Lead(
        source_id=source.source_id,
        region_name="North America",
        deal_value=Decimal("5000.00"),
        quality_score=80,
    )
    l2 = Lead(
        source_id=source.source_id,
        region_name="North America",
        deal_value=Decimal("10000.00"),
        quality_score=90,
    )
    # Lead for Europe
    l3 = Lead(
        source_id=source.source_id,
        region_name="Europe",
        deal_value=Decimal("4000.00"),
        quality_score=70,
    )
    # Unmapped leads (explicit None, empty string, and explicit "Unmapped Region")
    l4 = Lead(
        source_id=source.source_id,
        region_name=None,
        deal_value=Decimal("2000.00"),
        quality_score=60,
    )
    l5 = Lead(
        source_id=source.source_id,
        region_name="",
        deal_value=Decimal("1000.00"),
        quality_score=50,
    )

    test_session.add_all([l1, l2, l3, l4, l5])
    test_session.commit()

    results = get_territory_density_metrics(test_session)
    assert len(results) == 3

    # Ordered by opportunity count descending: North America (2), Unmapped Region (2), Europe (1)
    na_metric = next(m for m in results if m.region_name == "North America")
    assert na_metric.opportunity_count == 2
    assert na_metric.total_deal_value == 15000.0
    assert na_metric.average_quality_score == 85.0
    assert na_metric.density_score == 40.0
    assert na_metric.latitude == 37.0902

    unmapped_metric = next(
        m for m in results if m.region_name == settings.DEFAULT_UNMAPPED_REGION_NAME
    )
    assert unmapped_metric.opportunity_count == 2
    assert unmapped_metric.total_deal_value == 3000.0
    assert unmapped_metric.average_quality_score == 55.0
    assert unmapped_metric.density_score == 40.0

    europe_metric = next(m for m in results if m.region_name == "Europe")
    assert europe_metric.opportunity_count == 1
    assert europe_metric.total_deal_value == 4000.0
    assert europe_metric.average_quality_score == 70.0
    assert europe_metric.density_score == 20.0


def test_get_territory_density_metrics_filtering(test_session: Session):
    """Verify filtering by region, min deal size, min quality score, timestamp range, and source."""
    source1 = LeadSource(source_name="Source One")
    source2 = LeadSource(source_name="Source Two")
    test_session.add_all([source1, source2])
    test_session.commit()

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=10)

    l1 = Lead(
        source_id=source1.source_id,
        region_name="North America",
        deal_value=Decimal("10000.00"),
        quality_score=90,
        created_timestamp=now,
    )
    l2 = Lead(
        source_id=source1.source_id,
        region_name="North America",
        deal_value=Decimal("2000.00"),
        quality_score=40,
        created_timestamp=now,
    )
    l3 = Lead(
        source_id=source2.source_id,
        region_name="Europe",
        deal_value=Decimal("15000.00"),
        quality_score=95,
        created_timestamp=old_time,
    )
    test_session.add_all([l1, l2, l3])
    test_session.commit()

    # Filter by region_name
    na_only = get_territory_density_metrics(test_session, region_name="North America")
    assert len(na_only) == 1
    assert na_only[0].region_name == "North America"
    assert na_only[0].opportunity_count == 2

    # Filter by min_deal_size
    big_deals = get_territory_density_metrics(test_session, min_deal_size=5000)
    assert len(big_deals) == 2
    for metric in big_deals:
        assert metric.total_deal_value >= 5000.0

    # Filter by min_quality_score
    high_quality = get_territory_density_metrics(test_session, min_quality_score=85)
    assert len(high_quality) == 2

    # Filter by start_timestamp
    recent = get_territory_density_metrics(
        test_session, start_timestamp=now - timedelta(days=1)
    )
    assert len(recent) == 1
    assert recent[0].region_name == "North America"

    # Filter by source_id
    src2_metrics = get_territory_density_metrics(test_session, source_id=source2.source_id)
    assert len(src2_metrics) == 1
    assert src2_metrics[0].region_name == "Europe"


def test_get_unmapped_region_metrics(test_session: Session):
    """Verify helper function retrieving metrics specifically for unmapped region leads."""
    source = LeadSource(source_name="Direct")
    test_session.add(source)
    test_session.commit()

    # No unmapped leads initially
    assert get_unmapped_region_metrics(test_session) is None

    # Add unmapped lead
    lead = Lead(
        source_id=source.source_id,
        region_name=None,
        deal_value=Decimal("2500.00"),
        quality_score=75,
    )
    test_session.add(lead)
    test_session.commit()

    unmapped = get_unmapped_region_metrics(test_session)
    assert unmapped is not None
    assert unmapped.region_name == settings.DEFAULT_UNMAPPED_REGION_NAME
    assert unmapped.opportunity_count == 1
    assert unmapped.total_deal_value == 2500.0


def test_territory_density_metrics_to_dict():
    """Verify to_dict conversion method on TerritoryDensityMetrics."""
    metric = TerritoryDensityMetrics(
        region_name="APAC",
        opportunity_count=3,
        total_deal_value=12000.0,
        average_quality_score=82.5,
        density_score=15.0,
        latitude=1.3521,
        longitude=103.8198,
    )
    d = metric.to_dict()
    assert d["region_name"] == "APAC"
    assert d["opportunity_count"] == 3
    assert d["total_deal_value"] == 12000.0
    assert d["average_quality_score"] == 82.5
    assert d["density_score"] == 15.0
    assert d["latitude"] == 1.3521
    assert d["longitude"] == 103.8198
