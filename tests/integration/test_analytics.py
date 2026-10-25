"""Integration tests for Lead Acquisition Funnel Analytics, ROI Edge Cases, Territory Heat Maps, Brief Synthesis, and Campaign Comparisons.

Location: backend/tests/integration/test_analytics.py
Requirements: [FR-1, FR-2, FR-3, FR-8, FR-10]
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.acquisition import router as acquisition_router
from src.api.campaigns import router as campaigns_router
from src.api.funnel import router as funnel_router
from src.api.meetings import router as meetings_router
from src.api.territory import router as territory_router
from src.lib.config import settings
from src.models.base import Base, create_db_engine, get_db
from src.models.campaign import Campaign
from src.models.funnel import FunnelStage
from src.models.lead import Lead, LeadSource
from src.models.meeting import MeetingBrief, ScheduledMeeting
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel
from src.services.analytics.funnel_service import (
    calculate_lead_time_in_stage,
    calculate_stage_conversion_rate,
    detect_stage_bottleneck,
    get_funnel_stage_metrics,
    get_funnel_summary,
    get_leads_by_funnel_stage,
)
from src.services.analytics.roi_service import (
    calculate_roi_percentage,
    get_acquisition_summary,
    get_campaign_roi_metrics,
    get_lead_source_roi_metrics,
)
from src.services.analytics.territory_service import (
    calculate_density_score,
    get_region_coordinates,
    get_territory_density_metrics,
    get_territory_summary,
    normalize_region_name,
)
from src.services.campaigns.comparison_service import (
    compare_campaigns,
    get_campaign_comparison_matrix,
)
from src.services.meetings.brief_service import (
    generate_meeting_brief,
    get_meeting_brief,
    synthesize_interaction_history_summary,
    synthesize_key_discussion_notes,
)


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def test_session_token(test_db):
    """Fixture creating tenant, user, and active session records, returning session token string."""
    tenant = TenantModel(name="Integration Test Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="analytics.tester@example.com",
        password_hash="hashed_secret_key",
        full_name="Analytics Tester",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc).replace(year=2099),
    )
    test_db.add(session_record)
    test_db.commit()

    return session_record.id


@pytest.fixture
def client(test_db):
    """Fixture providing a FastAPI TestClient with all analytics routers and DB override."""
    app = FastAPI()
    app.include_router(acquisition_router)
    app.include_router(funnel_router)
    app.include_router(territory_router)
    app.include_router(meetings_router)
    app.include_router(campaigns_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


# ============================================================================
# 1. ROI FORMULA & ZERO-REVENUE / ZERO-COST EDGE CASE TESTS (FR-1, FR-2)
# ============================================================================


def test_calculate_roi_percentage_edge_cases():
    """Validate ROI percentage formula and zero/negative division edge cases."""
    # Standard positive ROI: revenue $5000, cost $1000 -> ((5000-1000)/1000)*100 = 400.0%
    assert calculate_roi_percentage(Decimal("5000.00"), Decimal("1000.00")) == 400.0
    assert calculate_roi_percentage(5000.0, 1000.0) == 400.0

    # Zero revenue edge case -> returns 0.0%
    assert calculate_roi_percentage(0, Decimal("1000.00")) == 0.0
    assert calculate_roi_percentage(None, Decimal("1000.00")) == 0.0

    # Zero or negative cost edge case -> returns 0.0%
    assert calculate_roi_percentage(Decimal("5000.00"), 0) == 0.0
    assert calculate_roi_percentage(Decimal("5000.00"), -100) == 0.0
    assert calculate_roi_percentage(Decimal("5000.00"), None) == 0.0

    # Both zero cost and zero revenue -> returns 0.0%
    assert calculate_roi_percentage(0, 0) == 0.0

    # Revenue less than cost (negative ROI mathematically): e.g. revenue $500, cost $1000 -> ((500-1000)/1000)*100 = -50.0%
    assert calculate_roi_percentage(500.0, 1000.0) == -50.0


def test_get_lead_source_roi_metrics_db_query(test_db):
    """Validate database queries and ROI calculations for lead acquisition sources (FR-1, FR-2)."""
    source_paid = LeadSource(
        source_name="Google Paid Ads",
        cost_amount=Decimal("2000.00"),
    )
    source_organic = LeadSource(
        source_name="Organic Search",
        cost_amount=Decimal("0.00"),  # Zero cost source
    )
    source_empty = LeadSource(
        source_name="Billboard Ads",
        cost_amount=Decimal("5000.00"),  # No leads attributed
    )
    test_db.add_all([source_paid, source_organic, source_empty])
    test_db.commit()

    # Create leads for paid source
    lead1 = Lead(
        source_id=source_paid.source_id,
        quality_score=80,
        deal_value=Decimal("6000.00"),
        region_name="North America",
    )
    lead2 = Lead(
        source_id=source_paid.source_id,
        quality_score=60,
        deal_value=Decimal("4000.00"),
        region_name="North America",
    )
    # Create lead for organic source (zero cost, positive deal value)
    lead3 = Lead(
        source_id=source_organic.source_id,
        quality_score=90,
        deal_value=Decimal("3000.00"),
        region_name="Europe",
    )
    test_db.add_all([lead1, lead2, lead3])
    test_db.commit()

    metrics = get_lead_source_roi_metrics(test_db)
    assert len(metrics) == 3

    # Find paid source metric
    paid_m = next(m for m in metrics if m.source_name == "Google Paid Ads")
    assert paid_m.total_leads == 2
    assert paid_m.average_quality_score == 70.0
    assert paid_m.total_spend == 2000.0
    assert paid_m.attributed_revenue == 10000.0
    # ROI: ((10000 - 2000) / 2000) * 100 = 400.0%
    assert paid_m.calculated_roi_percentage == 400.0

    # Organic source metric (zero cost -> ROI 0.0%)
    organic_m = next(m for m in metrics if m.source_name == "Organic Search")
    assert organic_m.total_leads == 1
    assert organic_m.average_quality_score == 90.0
    assert organic_m.total_spend == 0.0
    assert organic_m.attributed_revenue == 3000.0
    assert organic_m.calculated_roi_percentage == 0.0

    # Empty source metric (no leads -> revenue 0.0 -> ROI 0.0%)
    empty_m = next(m for m in metrics if m.source_name == "Billboard Ads")
    assert empty_m.total_leads == 0
    assert empty_m.average_quality_score == 0.0
    assert empty_m.total_spend == 5000.0
    assert empty_m.attributed_revenue == 0.0
    assert empty_m.calculated_roi_percentage == 0.0


def test_get_acquisition_summary_db_query_and_filters(test_db):
    """Validate acquisition summary aggregate queries and filtering options (FR-1, FR-2)."""
    source = LeadSource(
        source_name="Social Media",
        cost_amount=Decimal("1000.00"),
    )
    test_db.add(source)
    test_db.commit()

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=30)

    lead_recent = Lead(
        source_id=source.source_id,
        quality_score=75,
        deal_value=Decimal("5000.00"),
        region_name="EMEA",
        created_timestamp=now,
    )
    lead_old = Lead(
        source_id=source.source_id,
        quality_score=50,
        deal_value=Decimal("2000.00"),
        region_name="APAC",
        created_timestamp=old_time,
    )
    test_db.add_all([lead_recent, lead_old])
    test_db.commit()

    # Summary without filters
    summary = get_acquisition_summary(test_db)
    assert summary["total_sources"] == 1
    assert summary["total_leads"] == 2
    assert summary["overall_average_quality_score"] == 62.5
    assert summary["total_spend"] == 1000.0
    assert summary["total_attributed_revenue"] == 7000.0
    assert summary["overall_roi_percentage"] == 600.0

    # Summary with region filter
    emea_summary = get_acquisition_summary(test_db, region_name="EMEA")
    assert emea_summary["total_leads"] == 1
    assert emea_summary["total_attributed_revenue"] == 5000.0


# ============================================================================
# 2. CONVERSION FUNNEL & BOTTLENECK DETECTION TESTS (FR-3)
# ============================================================================


def test_conversion_rate_and_bottleneck_detection():
    """Validate stage conversion rate math and bottleneck threshold detection (FR-3)."""
    # First stage (previous_count is None)
    assert calculate_stage_conversion_rate(10, None) == 100.0
    assert calculate_stage_conversion_rate(0, None) == 0.0

    # Subsequent stages
    assert calculate_stage_conversion_rate(5, 10) == 50.0
    assert calculate_stage_conversion_rate(0, 10) == 0.0
    assert calculate_stage_conversion_rate(5, 0) == 0.0

    # Bottleneck detection: target 60%, actual 50% -> True
    assert detect_stage_bottleneck(50.0, target_conversion_rate=60.0) is True
    # Target 40%, actual 50% -> False
    assert detect_stage_bottleneck(50.0, target_conversion_rate=40.0) is False
    # Target 0 / unconfigured -> uses default fallback threshold
    default_target = float(settings.FUNNEL_BOTTLENECK_TARGET_CONVERSION_RATE)
    assert detect_stage_bottleneck(default_target - 5.0, target_conversion_rate=0.0) is True
    assert detect_stage_bottleneck(default_target + 5.0, target_conversion_rate=0.0) is False


def test_get_funnel_stage_metrics_db_query(test_db):
    """Validate database queries for funnel stages, conversion rates, and bottlenecks (FR-3)."""
    stage1 = FunnelStage(
        stage_name="Lead Inbound",
        stage_order=1,
        target_conversion_rate=Decimal("80.00"),
    )
    stage2 = FunnelStage(
        stage_name="Qualified Lead",
        stage_order=2,
        target_conversion_rate=Decimal("60.00"),
    )
    stage3 = FunnelStage(
        stage_name="Closed Deal",
        stage_order=3,
        target_conversion_rate=Decimal("50.00"),
    )
    test_db.add_all([stage1, stage2, stage3])
    test_db.commit()

    source = LeadSource(source_name="Inbound Web", cost_amount=Decimal("500.00"))
    test_db.add(source)
    test_db.commit()

    # 4 leads in stage1, 1 lead in stage2, 0 leads in stage3
    leads_s1 = [
        Lead(source_id=source.source_id, status_stage="Lead Inbound", quality_score=60)
        for _ in range(4)
    ]
    lead_s2 = Lead(source_id=source.source_id, status_stage="Qualified Lead", quality_score=80)
    test_db.add_all(leads_s1 + [lead_s2])
    test_db.commit()

    stage_metrics = get_funnel_stage_metrics(test_db)
    assert len(stage_metrics) == 3

    # Stage 1: 4 leads (100% conversion)
    m1 = stage_metrics[0]
    assert m1.stage_name == "Lead Inbound"
    assert m1.lead_count == 4
    assert m1.conversion_rate_percentage == 100.0
    assert m1.is_bottleneck is False

    # Stage 2: 1 lead (1 out of 4 = 25% conversion, target 60% -> bottleneck!)
    m2 = stage_metrics[1]
    assert m2.stage_name == "Qualified Lead"
    assert m2.lead_count == 1
    assert m2.conversion_rate_percentage == 25.0
    assert m2.is_bottleneck is True

    # Stage 3: 0 leads (0 out of 1 = 0% conversion, target 50% -> bottleneck!)
    m3 = stage_metrics[2]
    assert m3.stage_name == "Closed Deal"
    assert m3.lead_count == 0
    assert m3.conversion_rate_percentage == 0.0
    assert m3.is_bottleneck is True

    # Validate funnel summary
    summary = get_funnel_summary(test_db)
    assert summary["total_stages"] == 3
    assert summary["total_leads_in_funnel"] == 5
    assert summary["bottleneck_stage_count"] == 2
    assert summary["overall_funnel_conversion_rate"] == 0.0


def test_get_leads_by_funnel_stage_drilldown(test_db):
    """Validate stage lead drill-down database query with pagination (FR-3)."""
    stage = FunnelStage(stage_name="Discovery", stage_order=1)
    test_db.add(stage)
    test_db.commit()

    source = LeadSource(source_name="Direct", cost_amount=Decimal("0.00"))
    test_db.add(source)
    test_db.flush()

    for i in range(5):
        lead = Lead(
            source_id=source.source_id,
            status_stage="Discovery",
            quality_score=50 + (i * 10),
            deal_value=Decimal("1000.00") * (i + 1),
        )
        test_db.add(lead)
    test_db.commit()

    # Paginated query
    paged_res = get_leads_by_funnel_stage(test_db, stage_id=stage.stage_id, page=1, limit=3)
    assert paged_res["total_leads"] == 5
    assert paged_res["page"] == 1
    assert paged_res["limit"] == 3
    assert len(paged_res["leads"]) == 3


# ============================================================================
# 3. GEOGRAPHIC TERRITORY HEAT MAP & UNMAPPED REGION TESTS (FR-8)
# ============================================================================


def test_territory_normalization_and_coordinates():
    """Validate geographic region normalization and coordinate lookup (FR-8)."""
    # Normalization
    assert normalize_region_name(" North America ") == "North America"
    assert normalize_region_name(None) == settings.DEFAULT_UNMAPPED_REGION_NAME
    assert normalize_region_name("   ") == settings.DEFAULT_UNMAPPED_REGION_NAME

    # Coordinates
    na_coords = get_region_coordinates("North America")
    assert na_coords["latitude"] == 37.0902
    assert na_coords["longitude"] == -95.7129

    unmapped_coords = get_region_coordinates("Unmapped Region")
    assert unmapped_coords["latitude"] == 0.0
    assert unmapped_coords["longitude"] == 0.0

    unknown_coords = get_region_coordinates("Unknown Custom Region")
    assert unknown_coords["latitude"] == 0.0
    assert unknown_coords["longitude"] == 0.0


def test_get_territory_density_metrics_db_query(test_db):
    """Validate territory density score calculations and missing location grouping (FR-8)."""
    source = LeadSource(source_name="Events", cost_amount=Decimal("1000.00"))
    test_db.add(source)
    test_db.commit()

    # Lead in North America
    lead_na = Lead(
        source_id=source.source_id,
        quality_score=80,
        deal_value=Decimal("10000.00"),
        region_name="North America",
    )
    # Lead in Europe
    lead_eu = Lead(
        source_id=source.source_id,
        quality_score=70,
        deal_value=Decimal("5000.00"),
        region_name="Europe",
    )
    # Lead with missing region -> should group as "Unmapped Region"
    lead_unmapped1 = Lead(
        source_id=source.source_id,
        quality_score=50,
        deal_value=Decimal("2000.00"),
        region_name=None,
    )
    lead_unmapped2 = Lead(
        source_id=source.source_id,
        quality_score=40,
        deal_value=Decimal("1000.00"),
        region_name="   ",
    )
    test_db.add_all([lead_na, lead_eu, lead_unmapped1, lead_unmapped2])
    test_db.commit()

    metrics = get_territory_density_metrics(test_db)
    assert len(metrics) == 3

    # Total leads = 4. Density score for Unmapped Region (2 leads) = 50.0%
    unmapped_m = next(m for m in metrics if m.region_name == "Unmapped Region")
    assert unmapped_m.opportunity_count == 2
    assert unmapped_m.density_score == 50.0
    assert unmapped_m.total_deal_value == 3000.0
    assert unmapped_m.average_quality_score == 45.0
    assert unmapped_m.latitude == 0.0
    assert unmapped_m.longitude == 0.0

    # NA metric (1 lead = 25.0% density score)
    na_m = next(m for m in metrics if m.region_name == "North America")
    assert na_m.opportunity_count == 1
    assert na_m.density_score == 25.0
    assert na_m.total_deal_value == 10000.0

    # Validate territory summary
    summary = get_territory_summary(test_db)
    assert summary["total_territories"] == 3
    assert summary["total_opportunity_volume"] == 4
    assert summary["total_deal_value"] == 18000.0


# ============================================================================
# 4. MEETING PREPARATION BRIEF SYNTHESIS TESTS (FR-1, FR-3 CONTEXT)
# ============================================================================


def test_meeting_brief_synthesis_no_prior_meetings(test_db):
    """Validate meeting brief synthesis for a lead with no prior interaction history."""
    source = LeadSource(source_name="Outbound Email", cost_amount=Decimal("100.00"))
    campaign = Campaign(campaign_name="Q1 Outbound Push")
    test_db.add_all([source, campaign])
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=85,
        status_stage="QUALIFIED",
        region_name="North America",
        deal_value=Decimal("15000.00"),
    )
    test_db.add(lead)
    test_db.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Initial Solution Alignment",
        start_timestamp=datetime.now(timezone.utc),
    )
    test_db.add(meeting)
    test_db.commit()

    # Generate brief
    brief = generate_meeting_brief(test_db, meeting.meeting_id)
    assert brief is not None
    assert brief.meeting_id == meeting.meeting_id
    assert brief.lead_id == lead.lead_id
    assert brief.quality_score == 85
    assert "No prior meetings recorded" in brief.interaction_history_summary
    assert "Outbound Email" in brief.interaction_history_summary
    assert "Q1 Outbound Push" in brief.interaction_history_summary
    assert "Requirements Mapping" in brief.key_discussion_notes
    assert "High Quality Score: 85/100" in brief.key_discussion_notes
    assert "High Value Opportunity ($15,000.00)" in brief.key_discussion_notes

    # Verify meeting record was updated with brief summary
    test_db.refresh(meeting)
    assert meeting.brief_summary_text == brief.brief_summary_text


def test_meeting_brief_synthesis_with_prior_meetings(test_db):
    """Validate meeting brief synthesis incorporating prior meeting history and refresh (FR-1)."""
    source = LeadSource(source_name="Partner Lead", cost_amount=Decimal("0.00"))
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=60,
        status_stage="DEMO",
        deal_value=Decimal("8000.00"),
    )
    test_db.add(lead)
    test_db.commit()

    # Create prior meeting
    prior_meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Discovery Call",
        start_timestamp=datetime.now(timezone.utc) - timedelta(days=7),
        brief_summary_text="Initial discovery call completed.",
    )
    # Create current meeting
    current_meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Technical Deep Dive & Demo",
        start_timestamp=datetime.now(timezone.utc),
    )
    test_db.add_all([prior_meeting, current_meeting])
    test_db.commit()

    brief = generate_meeting_brief(test_db, current_meeting.meeting_id)
    assert brief is not None
    assert "1 prior meeting(s)" in brief.interaction_history_summary
    assert "Discovery Call" in brief.interaction_history_summary
    assert "Initial discovery call completed." in brief.interaction_history_summary
    assert "Product Demonstration" in brief.key_discussion_notes

    # Test force refresh
    lead.quality_score = 95
    test_db.commit()

    refreshed_brief = generate_meeting_brief(
        test_db, current_meeting.meeting_id, force_refresh=True
    )
    assert refreshed_brief.quality_score == 95


def test_generate_meeting_brief_not_found(test_db):
    """Validate graceful handling when brief is requested for non-existent meeting ID."""
    res = generate_meeting_brief(test_db, "non-existent-uuid")
    assert res is None


# ============================================================================
# 5. SIDE-BY-SIDE CAMPAIGN MATRIX COMPARISON TESTS (FR-10)
# ============================================================================


def test_compare_campaigns_db_query(test_db):
    """Validate side-by-side campaign comparison matrix queries and summary metrics (FR-10)."""
    camp1 = Campaign(
        campaign_name="Alpha Campaign",
        budget_amount=Decimal("10000.00"),
        spent_amount=Decimal("5000.00"),
    )
    camp2 = Campaign(
        campaign_name="Beta Campaign",
        budget_amount=Decimal("15000.00"),
        spent_amount=Decimal("10000.00"),
    )
    test_db.add_all([camp1, camp2])
    test_db.commit()

    source = LeadSource(source_name="Campaign Source", cost_amount=Decimal("0.00"))
    test_db.add(source)
    test_db.commit()

    # Leads for Camp1: 2 leads, $20,000 revenue
    lead_c1_1 = Lead(
        source_id=source.source_id,
        campaign_id=camp1.campaign_id,
        quality_score=80,
        deal_value=Decimal("12000.00"),
        status_stage="CLOSED_WON",
    )
    lead_c1_2 = Lead(
        source_id=source.source_id,
        campaign_id=camp1.campaign_id,
        quality_score=90,
        deal_value=Decimal("8000.00"),
        status_stage="CLOSED_WON",
    )
    # Leads for Camp2: 1 lead, $5,000 revenue
    lead_c2_1 = Lead(
        source_id=source.source_id,
        campaign_id=camp2.campaign_id,
        quality_score=50,
        deal_value=Decimal("5000.00"),
        status_stage="CLOSED_WON",
    )
    test_db.add_all([lead_c1_1, lead_c1_2, lead_c2_1])
    test_db.commit()

    matrix = compare_campaigns(test_db, campaign_ids=[camp1.campaign_id, camp2.campaign_id])
    assert len(matrix.campaigns) == 2

    c1_item = next(c for c in matrix.campaigns if c.campaign_name == "Alpha Campaign")
    assert c1_item.leads_generated == 2
    assert c1_item.average_quality_score == 85.0
    assert c1_item.attributed_revenue == 20000.0
    assert c1_item.spent_amount == 5000.0
    # ROI for Alpha: ((20000 - 5000) / 5000) * 100 = 300.0%
    assert c1_item.total_roi_percentage == 300.0
    assert c1_item.cost_per_lead == 2500.0

    c2_item = next(c for c in matrix.campaigns if c.campaign_name == "Beta Campaign")
    assert c2_item.leads_generated == 1
    assert c2_item.average_quality_score == 50.0
    assert c2_item.attributed_revenue == 5000.0
    assert c2_item.spent_amount == 10000.0
    # ROI for Beta: ((5000 - 10000) / 10000) * 100 = -50.0%
    assert c2_item.total_roi_percentage == -50.0

    # Summary verification
    summary = matrix.summary
    assert summary.total_campaigns_compared == 2
    assert summary.best_roi_campaign_id == camp1.campaign_id
    assert summary.highest_lead_volume_campaign_id == camp1.campaign_id
    assert summary.total_spend_across_campaigns == 15000.0
    assert summary.total_revenue_across_campaigns == 25000.0
    assert summary.total_leads_across_campaigns == 3


# ============================================================================
# 6. FASTAPI ENDPOINT INTEGRATION TESTS VIA CLIENT
# ============================================================================


def test_api_acquisition_sources_integration(client, test_db, test_session_token):
    """Verify HTTP GET /api/v1/acquisition/sources returning populated metrics."""
    source = LeadSource(source_name="Search Ads", cost_amount=Decimal("1000.00"))
    test_db.add(source)
    test_db.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=90,
        deal_value=Decimal("5000.00"),
    )
    test_db.add(lead)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/acquisition/sources", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data) == 1
    assert data[0]["source_name"] == "Search Ads"
    assert data[0]["calculated_roi_percentage"] == 400.0


def test_api_funnel_stages_integration(client, test_db, test_session_token):
    """Verify HTTP GET /api/v1/funnel/stages returning stage metrics and bottleneck flags."""
    stage = FunnelStage(stage_name="Inbound", stage_order=1, target_conversion_rate=Decimal("50.00"))
    test_db.add(stage)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/funnel/stages", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data) == 1
    assert data[0]["stage_name"] == "Inbound"


def test_api_territory_heatmap_integration(client, test_db, test_session_token):
    """Verify HTTP GET /api/v1/territory/heatmap returning geographic density metrics."""
    source = LeadSource(source_name="Local Event", cost_amount=Decimal("0.00"))
    test_db.add(source)
    test_db.commit()

    lead = Lead(source_id=source.source_id, region_name="North America", quality_score=75)
    test_db.add(lead)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get("/api/v1/territory/heatmap", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data) >= 1
    na_data = next(d for d in data if d["region_name"] == "North America")
    assert na_data["opportunity_count"] == 1


def test_api_meetings_brief_integration(client, test_db, test_session_token):
    """Verify HTTP GET /api/v1/meetings/{id}/brief returning synthesized preparation brief."""
    source = LeadSource(source_name="Direct Contact", cost_amount=Decimal("0.00"))
    test_db.add(source)
    test_db.commit()

    lead = Lead(source_id=source.source_id, quality_score=70, status_stage="NEW")
    test_db.add(lead)
    test_db.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Alignment Call",
        start_timestamp=datetime.now(timezone.utc),
    )
    test_db.add(meeting)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get(f"/api/v1/meetings/{meeting.meeting_id}/brief", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["meeting_id"] == meeting.meeting_id
    assert "Preparation brief" in data["brief_summary_text"]


def test_api_campaigns_compare_integration(client, test_db, test_session_token):
    """Verify HTTP GET /api/v1/campaigns/compare returning campaign comparison matrix."""
    camp = Campaign(
        campaign_name="Spring Campaign",
        budget_amount=Decimal("5000.00"),
        spent_amount=Decimal("2000.00"),
    )
    test_db.add(camp)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}
    response = client.get(
        f"/api/v1/campaigns/compare?campaign_ids={camp.campaign_id}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert "campaigns" in data
    assert "summary" in data
    assert len(data["campaigns"]) == 1
    assert data["campaigns"][0]["campaign_name"] == "Spring Campaign"
