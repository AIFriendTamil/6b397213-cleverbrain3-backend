"""Contract verification tests validating backend API response payloads against OpenAPI specification in contracts/openapi.json."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Type

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter
from sqlalchemy.orm import Session as DBSession

from src.api.acquisition import AcquisitionSummarySchema, SourceROIMetricsSchema
from src.api.campaigns import CampaignComparisonMatrixSchema, CampaignSchema
from src.api.funnel import FunnelStageMetricsSchema, StageLeadDrillDownResponseSchema
from src.api.main import app
from src.api.meetings import MeetingBriefSchema, MeetingEventSchema
from src.api.territory import TerritoryDensityMetricsSchema
from src.models.base import Base, create_db_engine, get_db
from src.models.campaign import Campaign
from src.models.funnel import FunnelStage
from src.models.lead import Lead, LeadSource
from src.models.meeting import MeetingBrief, ScheduledMeeting
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel


def get_contracts_file_path() -> Path:
    """Return absolute filesystem path to contracts/openapi.json."""
    backend_dir = Path(__file__).resolve().parent.parent.parent
    project_root = backend_dir.parent
    return project_root / "contracts" / "openapi.json"


def sync_openapi_contract_file() -> Dict[str, Any]:
    """Sync and return contracts/openapi.json with FastAPI app.openapi() schema."""
    openapi_path = get_contracts_file_path()
    app_schema = app.openapi()
    openapi_path.parent.mkdir(parents=True, exist_ok=True)
    with open(openapi_path, "w", encoding="utf-8") as f:
        json.dump(app_schema, f, indent=2)
        f.write("\n")
    return app_schema


@pytest.fixture
def openapi_spec() -> Dict[str, Any]:
    """Fixture returning the synchronized OpenAPI specification dictionary."""
    return sync_openapi_contract_file()


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def test_session_token(test_db: DBSession) -> str:
    """Fixture creating tenant, user, and session records, returning active session token string."""
    tenant = TenantModel(name="Analytics Corp")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="analyst@example.com",
        password_hash="hashed_secret",
        full_name="Analytics User",
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
def client(test_db: DBSession) -> TestClient:
    """Fixture providing a FastAPI TestClient with overridden get_db dependency."""
    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def assert_payload_matches_schema(
    payload: Any,
    pydantic_schema: Type[BaseModel],
    openapi_spec: Dict[str, Any],
    is_list: bool = False,
) -> None:
    """Helper verifying response payload deserializes against Pydantic model and schema exists in OpenAPI spec."""
    schema_name = pydantic_schema.__name__
    schemas_in_spec = openapi_spec.get("components", {}).get("schemas", {})

    assert schema_name in schemas_in_spec, (
        f"Schema '{schema_name}' is missing from OpenAPI components.schemas specification."
    )

    if is_list:
        assert isinstance(payload, list), f"Expected response payload list, got {type(payload)}"
        adapter = TypeAdapter(List[pydantic_schema])  # type: ignore
        validated = adapter.validate_python(payload)
        assert len(validated) == len(payload)
    else:
        assert isinstance(payload, dict), f"Expected response payload dict, got {type(payload)}"
        pydantic_schema.model_validate(payload)


def test_openapi_schema_contract_structure(openapi_spec: Dict[str, Any]) -> None:
    """Verify contracts/openapi.json structure and presence of feature 005 API path definitions (NFR-2)."""
    assert "openapi" in openapi_spec
    assert openapi_spec["openapi"].startswith("3.")
    assert "paths" in openapi_spec
    assert "components" in openapi_spec

    paths = openapi_spec["paths"]
    expected_paths = [
        "/api/v1/acquisition/sources",
        "/api/v1/acquisition/summary",
        "/api/v1/funnel/stages",
        "/api/v1/funnel/bottlenecks",
        "/api/v1/funnel/stages/{stage_id}/leads",
        "/api/v1/territory/heatmap",
        "/api/v1/meetings",
        "/api/v1/meetings/{meeting_id}/brief",
        "/api/v1/campaigns",
        "/api/v1/campaigns/compare",
    ]

    for expected_path in expected_paths:
        assert expected_path in paths, f"Path '{expected_path}' missing from OpenAPI paths specification"


def test_validate_acquisition_api_responses_contract(
    client: TestClient,
    test_db: DBSession,
    test_session_token: str,
    openapi_spec: Dict[str, Any],
) -> None:
    """Validate Lead Acquisition source metrics and summary API responses against OpenAPI spec (NFR-2)."""
    source = LeadSource(source_name="Inbound Web", cost_amount=Decimal("1000.00"))
    test_db.add(source)
    test_db.flush()

    lead = Lead(
        source_id=source.source_id,
        quality_score=85,
        status_stage="Qualified",
        deal_value=Decimal("5000.00"),
    )
    test_db.add(lead)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}

    # 1. GET /api/v1/acquisition/sources
    resp_sources = client.get("/api/v1/acquisition/sources", headers=headers)
    assert resp_sources.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_sources.json(), SourceROIMetricsSchema, openapi_spec, is_list=True
    )

    # 2. GET /api/v1/acquisition/summary
    resp_summary = client.get("/api/v1/acquisition/summary", headers=headers)
    assert resp_summary.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_summary.json(), AcquisitionSummarySchema, openapi_spec, is_list=False
    )

    # 3. GET /api/v1/acquisition/sources/{source_id}
    resp_single = client.get(
        f"/api/v1/acquisition/sources/{source.source_id}", headers=headers
    )
    assert resp_single.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_single.json(), SourceROIMetricsSchema, openapi_spec, is_list=False
    )


def test_validate_funnel_api_responses_contract(
    client: TestClient,
    test_db: DBSession,
    test_session_token: str,
    openapi_spec: Dict[str, Any],
) -> None:
    """Validate Conversion Funnel stage metrics and lead drilldown API responses against OpenAPI spec (NFR-2)."""
    stage1 = FunnelStage(stage_name="Lead Received", stage_order=1, target_conversion_rate=Decimal("80.0"))
    stage2 = FunnelStage(stage_name="Demo Scheduled", stage_order=2, target_conversion_rate=Decimal("50.0"))
    test_db.add_all([stage1, stage2])
    test_db.flush()

    lead = Lead(
        source_id="dummy_source",
        quality_score=75,
        status_stage=stage1.stage_id,
        region_name="North America",
        deal_value=Decimal("2500.00"),
    )
    test_db.add(lead)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}

    # 1. GET /api/v1/funnel/stages
    resp_stages = client.get("/api/v1/funnel/stages", headers=headers)
    assert resp_stages.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_stages.json(), FunnelStageMetricsSchema, openapi_spec, is_list=True
    )

    # 2. GET /api/v1/funnel/bottlenecks
    resp_bottlenecks = client.get("/api/v1/funnel/bottlenecks", headers=headers)
    assert resp_bottlenecks.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_bottlenecks.json(), FunnelStageMetricsSchema, openapi_spec, is_list=True
    )

    # 3. GET /api/v1/funnel/stages/{stage_id}/leads
    resp_leads = client.get(f"/api/v1/funnel/stages/{stage1.stage_id}/leads", headers=headers)
    assert resp_leads.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_leads.json(), StageLeadDrillDownResponseSchema, openapi_spec, is_list=False
    )


def test_validate_territory_api_responses_contract(
    client: TestClient,
    test_db: DBSession,
    test_session_token: str,
    openapi_spec: Dict[str, Any],
) -> None:
    """Validate Geographic Territory opportunity heatmap API responses against OpenAPI spec (NFR-2)."""
    lead1 = Lead(
        source_id="dummy_source",
        quality_score=90,
        status_stage="Qualified",
        region_name="West Coast",
        state_code="CA",
        postal_code="90210",
        deal_value=Decimal("15000.00"),
    )
    lead2 = Lead(
        source_id="dummy_source",
        quality_score=60,
        status_stage="New",
        region_name="Unmapped Region",
        deal_value=Decimal("3000.00"),
    )
    test_db.add_all([lead1, lead2])
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}

    resp = client.get("/api/v1/territory/heatmap", headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp.json(), TerritoryDensityMetricsSchema, openapi_spec, is_list=True
    )


def test_validate_meetings_api_responses_contract(
    client: TestClient,
    test_db: DBSession,
    test_session_token: str,
    openapi_spec: Dict[str, Any],
) -> None:
    """Validate Sales Meeting calendar and preparation brief API responses against OpenAPI spec (NFR-2)."""
    lead = Lead(
        source_id="dummy_source",
        quality_score=80,
        status_stage="Discovery",
        deal_value=Decimal("10000.00"),
    )
    test_db.add(lead)
    test_db.flush()

    now = datetime.now(timezone.utc)
    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Strategy Review",
        start_timestamp=now,
        end_timestamp=now,
    )
    test_db.add(meeting)
    test_db.flush()

    brief = MeetingBrief(
        meeting_id=meeting.meeting_id,
        lead_id=lead.lead_id,
        quality_score=80,
        interaction_history_summary="Initial inquiry submitted.",
        key_discussion_notes="Discuss product integration options.",
        brief_summary_text="High priority lead review.",
    )
    test_db.add(brief)
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}

    # 1. GET /api/v1/meetings
    resp_meetings = client.get("/api/v1/meetings", headers=headers)
    assert resp_meetings.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_meetings.json(), MeetingEventSchema, openapi_spec, is_list=True
    )

    # 2. GET /api/v1/meetings/{meeting_id}/brief
    resp_brief = client.get(f"/api/v1/meetings/{meeting.meeting_id}/brief", headers=headers)
    assert resp_brief.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_brief.json(), MeetingBriefSchema, openapi_spec, is_list=False
    )


def test_validate_campaigns_api_responses_contract(
    client: TestClient,
    test_db: DBSession,
    test_session_token: str,
    openapi_spec: Dict[str, Any],
) -> None:
    """Validate Marketing Campaign matrix comparison API responses against OpenAPI spec (NFR-2)."""
    campaign1 = Campaign(
        campaign_name="Q1 Growth Search",
        budget_amount=Decimal("5000.00"),
        spent_amount=Decimal("2000.00"),
    )
    campaign2 = Campaign(
        campaign_name="Email Nurture Blitz",
        budget_amount=Decimal("3000.00"),
        spent_amount=Decimal("1500.00"),
    )
    test_db.add_all([campaign1, campaign2])
    test_db.commit()

    headers = {"Authorization": f"Bearer {test_session_token}"}

    # 1. GET /api/v1/campaigns
    resp_list = client.get("/api/v1/campaigns", headers=headers)
    assert resp_list.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_list.json(), CampaignSchema, openapi_spec, is_list=True
    )

    # 2. GET /api/v1/campaigns/compare
    query_url = (
        f"/api/v1/campaigns/compare"
        f"?campaign_ids={campaign1.campaign_id}&campaign_ids={campaign2.campaign_id}"
    )
    resp_compare = client.get(query_url, headers=headers)
    assert resp_compare.status_code == status.HTTP_200_OK
    assert_payload_matches_schema(
        resp_compare.json(), CampaignComparisonMatrixSchema, openapi_spec, is_list=False
    )


def test_validate_error_responses_contract(
    client: TestClient,
    test_session_token: str,
) -> None:
    """Validate HTTP error response payloads against expected error contracts (NFR-2, NFR-3)."""
    headers = {"Authorization": f"Bearer {test_session_token}"}

    # 1. HTTP 401 Unauthorized
    unauth_resp = client.get("/api/v1/acquisition/sources")
    assert unauth_resp.status_code == status.HTTP_401_UNAUTHORIZED
    data_401 = unauth_resp.json()
    assert "detail" in data_401
    assert data_401["detail"]["code"] == "UNAUTHORIZED"

    # 2. HTTP 400 Bad Request (Invalid date range)
    invalid_range_url = (
        "/api/v1/acquisition/sources"
        "?start_timestamp=2026-12-31T00:00:00Z&end_timestamp=2026-01-01T00:00:00Z"
    )
    bad_req_resp = client.get(invalid_range_url, headers=headers)
    assert bad_req_resp.status_code == status.HTTP_400_BAD_REQUEST
    data_400 = bad_req_resp.json()
    assert "detail" in data_400
    assert data_400["detail"]["code"] == "INVALID_DATE_RANGE"

    # 3. HTTP 404 Not Found (Missing resource)
    not_found_resp = client.get("/api/v1/meetings/non-existent-uuid/brief", headers=headers)
    assert not_found_resp.status_code == status.HTTP_404_NOT_FOUND
    data_404 = not_found_resp.json()
    assert "detail" in data_404
    assert data_404["detail"]["code"] == "MEETING_NOT_FOUND"
