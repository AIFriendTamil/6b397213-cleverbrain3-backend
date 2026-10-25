"""Integration tests for Deal Pipeline API endpoints in backend/src/api/deals.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.deal import Deal as DealModel, DealStage as DealStageModel, Milestone as MilestoneModel, MilestoneStatus
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        # Seed default deal stages
        s1 = DealStageModel(id="stage-1", name="Prospecting", display_order=1)
        s2 = DealStageModel(id="stage-2", name="Qualification", display_order=2)
        s3 = DealStageModel(id="stage-3", name="Closed Won", display_order=3)
        session.add_all([s1, s2, s3])
        session.commit()
        yield session


@pytest.fixture
def test_session_data(test_db):
    """Fixture creating tenant, user, and session records, returning session model."""
    tenant = TenantModel(name="Deal Test Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="sales_manager@example.com",
        password_hash="secret_hash",
        full_name="Sales Manager User",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def client(test_db):
    """Fixture overriding get_db dependency and returning a FastAPI TestClient instance."""
    app_instance = create_app()

    def _get_db_override():
        yield test_db

    app_instance.dependency_overrides[get_db] = _get_db_override
    with TestClient(app_instance) as test_client:
        yield test_client
    app_instance.dependency_overrides.clear()


# --- Unauthenticated Access Tests ---


def test_unauthenticated_access_denied(client):
    """Verify that unauthenticated requests return 401 Unauthorized."""
    res = client.get("/api/v1/deals/funnel")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    res = client.get("/api/v1/deals")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


# --- Pipeline Funnel & Deal Management Tests ---


def test_get_pipeline_funnel_empty(client, test_session_data):
    """Verify GET /api/v1/deals/funnel returns empty pipeline funnel summary."""
    headers = {"X-Session-ID": test_session_data.id}
    res = client.get("/api/v1/deals/funnel", headers=headers)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total_deals"] == 0
    assert len(data["stages"]) == 3


def test_create_and_list_deals(client, test_session_data):
    """Verify deal creation and filtering via API."""
    headers = {"X-Session-ID": test_session_data.id}

    # Create deal 1
    payload1 = {
        "title": "Enterprise Software License",
        "monetary_value": 50000.0,
        "stage_id": "stage-1",
        "win_probability": 0.3,
    }
    res1 = client.post("/api/v1/deals", json=payload1, headers=headers)
    assert res1.status_code == status.HTTP_201_CREATED
    deal1_data = res1.json()
    assert deal1_data["title"] == "Enterprise Software License"

    # Create deal 2
    payload2 = {
        "title": "Mid-Market Expansion",
        "monetary_value": 20000.0,
        "stage_id": "stage-2",
        "win_probability": 0.6,
    }
    res2 = client.post("/api/v1/deals", json=payload2, headers=headers)
    assert res2.status_code == status.HTTP_201_CREATED

    # List deals
    res_list = client.get("/api/v1/deals", headers=headers)
    assert res_list.status_code == status.HTTP_200_OK
    deals = res_list.json()
    assert len(deals) == 2

    # Get funnel
    res_funnel = client.get("/api/v1/deals/funnel", headers=headers)
    assert res_funnel.status_code == status.HTTP_200_OK
    funnel_data = res_funnel.json()
    assert funnel_data["total_deals"] == 2
    assert funnel_data["total_pipeline_value"] == 70000.0


def test_deal_crud_operations(client, test_session_data):
    """Verify GET, PUT, and DELETE /api/v1/deals/{deal_id}."""
    headers = {"X-Session-ID": test_session_data.id}

    # Create
    res_create = client.post(
        "/api/v1/deals",
        json={"title": "Test Deal CRUD", "monetary_value": 10000.0, "stage_id": "stage-1"},
        headers=headers,
    )
    deal_id = res_create.json()["id"]

    # Get single
    res_get = client.get(f"/api/v1/deals/{deal_id}", headers=headers)
    assert res_get.status_code == status.HTTP_200_OK
    assert res_get.json()["title"] == "Test Deal CRUD"

    # Update
    res_put = client.put(
        f"/api/v1/deals/{deal_id}",
        json={"title": "Updated Deal Title", "monetary_value": 15000.0},
        headers=headers,
    )
    assert res_put.status_code == status.HTTP_200_OK
    assert res_put.json()["title"] == "Updated Deal Title"

    # Delete - must return 204 with empty body
    res_del = client.delete(f"/api/v1/deals/{deal_id}", headers=headers)
    assert res_del.status_code == status.HTTP_204_NO_CONTENT
    assert res_del.content == b""

    # Verify 404 after deletion
    res_get_deleted = client.get(f"/api/v1/deals/{deal_id}", headers=headers)
    assert res_get_deleted.status_code == status.HTTP_404_NOT_FOUND


def test_stage_transition_api(client, test_session_data):
    """Verify POST /api/v1/deals/{deal_id}/stage transitions stage."""
    headers = {"X-Session-ID": test_session_data.id}

    res_create = client.post(
        "/api/v1/deals",
        json={"title": "Transition Deal", "monetary_value": 30000.0, "stage_id": "stage-1"},
        headers=headers,
    )
    deal_id = res_create.json()["id"]

    res_trans = client.post(
        f"/api/v1/deals/{deal_id}/stage?stage_id=stage-2",
        headers=headers,
    )
    assert res_trans.status_code == status.HTTP_200_OK
    assert res_trans.json()["stage_id"] == "stage-2"


# --- Milestone & Critical Task Tests ---


def test_milestones_and_tasks_api(client, test_session_data):
    """Verify milestone creation, retrieval, status evaluation, and task creation."""
    headers = {"X-Session-ID": test_session_data.id}

    res_deal = client.post(
        "/api/v1/deals",
        json={"title": "Milestone Test Deal", "stage_id": "stage-1"},
        headers=headers,
    )
    deal_id = res_deal.json()["id"]

    # Create milestone
    target_dt = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    res_ms = client.post(
        f"/api/v1/deals/{deal_id}/milestones",
        json={"title": "Technical Review", "target_date": target_dt, "status": "IN_PROGRESS"},
        headers=headers,
    )
    assert res_ms.status_code == status.HTTP_201_CREATED
    ms_data = res_ms.json()
    ms_id = ms_data["id"]
    assert ms_data["color_code"] == "blue"

    # Create task under milestone
    due_dt = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    res_task = client.post(
        f"/api/v1/deals/{deal_id}/milestones/{ms_id}/tasks",
        json={"title": "Security Architecture Approval", "due_date": due_dt, "is_critical": True},
        headers=headers,
    )
    assert res_task.status_code == status.HTTP_201_CREATED
    task_data = res_task.json()
    assert task_data["title"] == "Security Architecture Approval"

    # Get milestones
    res_get_ms = client.get(f"/api/v1/deals/{deal_id}/milestones", headers=headers)
    assert res_get_ms.status_code == status.HTTP_200_OK
    ms_list = res_get_ms.json()
    assert len(ms_list) == 1
    assert len(ms_list[0]["critical_tasks"]) == 1


# --- Stakeholders Network Tests ---


def test_stakeholders_api(client, test_session_data):
    """Verify stakeholder contact creation and graph network retrieval."""
    headers = {"X-Session-ID": test_session_data.id}

    res_deal = client.post(
        "/api/v1/deals",
        json={"title": "Stakeholder Test Deal", "stage_id": "stage-1"},
        headers=headers,
    )
    deal_id = res_deal.json()["id"]

    # Create parent stakeholder
    res_st1 = client.post(
        f"/api/v1/deals/{deal_id}/stakeholders",
        json={
            "name": "Jane VP",
            "org_role": "VP of Engineering",
            "contact_email": "jane@example.com",
        },
        headers=headers,
    )
    assert res_st1.status_code == status.HTTP_201_CREATED
    st1_id = res_st1.json()["id"]

    # Create subordinate stakeholder
    res_st2 = client.post(
        f"/api/v1/deals/{deal_id}/stakeholders",
        json={
            "name": "Bob Lead",
            "org_role": "Tech Lead",
            "contact_email": "bob@example.com",
            "parent_stakeholder_id": st1_id,
        },
        headers=headers,
    )
    assert res_st2.status_code == status.HTTP_201_CREATED

    # Get graph
    res_graph = client.get(f"/api/v1/deals/{deal_id}/stakeholders", headers=headers)
    assert res_graph.status_code == status.HTTP_200_OK
    graph_data = res_graph.json()
    assert len(graph_data["nodes"]) == 2
    assert len(graph_data["edges"]) == 1
    assert graph_data["has_cycles"] is False


def test_stakeholders_api_error_cases(client, test_session_data):
    """Verify error cases for stakeholder network endpoints."""
    headers = {"X-Session-ID": test_session_data.id}

    # 404 on non-existent deal for stakeholder list
    res_404 = client.get("/api/v1/deals/non-existent-id/stakeholders", headers=headers)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND

    # 404 on non-existent deal for stakeholder creation
    res_create_404 = client.post(
        "/api/v1/deals/non-existent-id/stakeholders",
        json={"name": "Ghost User", "org_role": "Advisor", "contact_email": "ghost@example.com"},
        headers=headers,
    )
    assert res_create_404.status_code == status.HTTP_404_NOT_FOUND

    # 400 on invalid parent stakeholder ID
    res_deal = client.post(
        "/api/v1/deals",
        json={"title": "Parent Err Deal", "stage_id": "stage-1"},
        headers=headers,
    )
    deal_id = res_deal.json()["id"]

    res_invalid_parent = client.post(
        f"/api/v1/deals/{deal_id}/stakeholders",
        json={
            "name": "Bob Lead",
            "org_role": "Tech Lead",
            "contact_email": "bob@example.com",
            "parent_stakeholder_id": "invalid-parent-id",
        },
        headers=headers,
    )
    assert res_invalid_parent.status_code == status.HTTP_400_BAD_REQUEST


# --- Timeline Forecast Tests ---


def test_deal_forecast_api(client, test_session_data):
    """Verify POST /api/v1/deals/{deal_id}/forecast returns timeline prediction."""
    headers = {"X-Session-ID": test_session_data.id}

    res_deal = client.post(
        "/api/v1/deals",
        json={"title": "Forecast Test Deal", "stage_id": "stage-1"},
        headers=headers,
    )
    deal_id = res_deal.json()["id"]

    res_fc = client.post(
        f"/api/v1/deals/{deal_id}/forecast",
        json={"include_milestones": True},
        headers=headers,
    )
    assert res_fc.status_code == status.HTTP_200_OK
    fc_data = res_fc.json()
    assert fc_data["deal_id"] == deal_id
    assert "predicted_close_date" in fc_data
    assert "confidence_score" in fc_data


def test_deal_forecast_api_not_found(client, test_session_data):
    """Verify 404 when predicting forecast for non-existent deal."""
    headers = {"X-Session-ID": test_session_data.id}
    res_404 = client.post("/api/v1/deals/non-existent-id/forecast", headers=headers)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND
