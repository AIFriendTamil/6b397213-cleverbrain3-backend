"""Integration tests for quote status listing and approval submission API endpoints in backend/src/api/quote_routes.py."""

import uuid
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.quote import Quote, QuoteApprovalStatus, QuoteStatus
from src.services.financial.quote import create_quote, process_quote_approval, submit_quote_for_approval


@pytest.fixture
def test_db():
    """Fixture providing an ephemeral in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def client(test_db: DBSession):
    """Fixture providing a TestClient with database session dependency override."""
    app_instance = create_app()

    def _override_get_db():
        try:
            yield test_db
        finally:
            pass

    app_instance.dependency_overrides[get_db] = _override_get_db
    with TestClient(app_instance) as test_client:
        yield test_client


def test_list_quotes_endpoint_empty(client: TestClient):
    """Verify GET /api/v1/financial/quotes returns an empty list when no quotes exist."""
    response = client.get("/api/v1/financial/quotes")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_create_and_get_quote_endpoints(client: TestClient):
    """Verify POST /api/v1/financial/quotes creates a quote and GET retrieves it."""
    account_id = str(uuid.uuid4())
    payload = {
        "account_id": account_id,
        "title": "Enterprise Cloud Migration Quote",
        "total_amount": 75000.0,
        "approver_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
    }

    create_res = client.post("/api/v1/financial/quotes", json=payload)
    assert create_res.status_code == status.HTTP_201_CREATED
    data = create_res.json()

    quote_id = data["id"]
    assert data["account_id"] == account_id
    assert data["title"] == "Enterprise Cloud Migration Quote"
    assert data["total_amount"] == 75000.0
    assert data["status"] == "PENDING_APPROVAL"
    assert len(data["approval_steps"]) == 2
    assert data["approval_steps"][0]["step_number"] == 1
    assert data["approval_steps"][1]["step_number"] == 2

    # Get quote by ID
    get_res = client.get(f"/api/v1/financial/quotes/{quote_id}")
    assert get_res.status_code == status.HTTP_200_OK
    get_data = get_res.json()
    assert get_data["id"] == quote_id
    assert get_data["title"] == "Enterprise Cloud Migration Quote"


def test_list_quotes_filtering(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/financial/quotes filtering by account_id and status."""
    acc_id_1 = str(uuid.uuid4())
    acc_id_2 = str(uuid.uuid4())

    q1 = create_quote(test_db, acc_id_1, "Quote 1", 10000)
    q2 = create_quote(test_db, acc_id_1, "Quote 2", 20000, approver_ids=[str(uuid.uuid4())])
    q3 = create_quote(test_db, acc_id_2, "Quote 3", 30000)

    # Filter by account_id
    res_acc1 = client.get(f"/api/v1/financial/quotes?account_id={acc_id_1}")
    assert res_acc1.status_code == status.HTTP_200_OK
    data_acc1 = res_acc1.json()
    assert len(data_acc1) == 2

    # Filter by status
    res_draft = client.get("/api/v1/financial/quotes?status=DRAFT")
    assert res_draft.status_code == status.HTTP_200_OK
    data_draft = res_draft.json()
    assert len(data_draft) == 2

    res_pending = client.get("/api/v1/financial/quotes?status=PENDING_APPROVAL")
    assert res_pending.status_code == status.HTTP_200_OK
    data_pending = res_pending.json()
    assert len(data_pending) == 1
    assert data_pending[0]["id"] == q2.id


def test_submit_quote_for_approval_endpoint(client: TestClient, test_db: DBSession):
    """Verify POST /api/v1/financial/quotes/{quote_id}/submit transitions draft quote to pending approval."""
    acc_id = str(uuid.uuid4())
    q = create_quote(test_db, acc_id, "Draft Quote", 15000)

    app1 = str(uuid.uuid4())
    app2 = str(uuid.uuid4())

    submit_res = client.post(
        f"/api/v1/financial/quotes/{q.id}/submit",
        json={"approver_ids": [app1, app2]},
    )
    assert submit_res.status_code == status.HTTP_200_OK
    data = submit_res.json()
    assert data["status"] == "PENDING_APPROVAL"
    assert len(data["approval_steps"]) == 2
    assert data["approval_steps"][0]["approver_id"] == app1
    assert data["approval_steps"][1]["approver_id"] == app2


def test_approve_quote_workflow_steps(client: TestClient, test_db: DBSession):
    """Verify multi-step approval workflow via POST /api/v1/financial/quotes/{quote_id}/approve."""
    acc_id = str(uuid.uuid4())
    app1 = str(uuid.uuid4())
    app2 = str(uuid.uuid4())

    q = create_quote(test_db, acc_id, "Workflow Deal", 50000, approver_ids=[app1, app2])

    # Approve Step 1
    res_step1 = client.post(
        f"/api/v1/financial/quotes/{q.id}/approve",
        json={
            "step_number": 1,
            "decision": "APPROVED",
            "comment": "Step 1 looks great.",
            "approver_id": app1,
        },
    )
    assert res_step1.status_code == status.HTTP_200_OK
    data_s1 = res_step1.json()
    assert data_s1["status"] == "PENDING_APPROVAL"
    assert data_s1["approval_steps"][0]["status"] == "APPROVED"
    assert data_s1["approval_steps"][0]["comment"] == "Step 1 looks great."

    # Approve Step 2 (Final approval)
    res_step2 = client.post(
        f"/api/v1/financial/quotes/{q.id}/approve",
        json={
            "step_number": 2,
            "decision": "APPROVED",
            "comment": "Step 2 final sign-off.",
            "approver_id": app2,
        },
    )
    assert res_step2.status_code == status.HTTP_200_OK
    data_s2 = res_step2.json()
    assert data_s2["status"] == "APPROVED"
    assert data_s2["approval_steps"][1]["status"] == "APPROVED"


def test_approve_quote_out_of_order_conflict(client: TestClient, test_db: DBSession):
    """Verify 409 Conflict when attempting out-of-order approval (Step 2 before Step 1)."""
    acc_id = str(uuid.uuid4())
    app1 = str(uuid.uuid4())
    app2 = str(uuid.uuid4())

    q = create_quote(test_db, acc_id, "Out of Order Test", 30000, approver_ids=[app1, app2])

    res = client.post(
        f"/api/v1/financial/quotes/{q.id}/approve",
        json={
            "step_number": 2,
            "decision": "APPROVED",
            "approver_id": app2,
        },
    )
    assert res.status_code == status.HTTP_409_CONFLICT
    err_detail = res.json()["detail"]
    assert err_detail["code"] == "QUOTE_APPROVAL_CONFLICT"
    assert "Out-of-order approval submission" in err_detail["message"]


def test_approve_quote_not_found_and_rejection(client: TestClient, test_db: DBSession):
    """Verify 404 Not Found for missing quote and REJECTED workflow transition."""
    fake_id = str(uuid.uuid4())
    res_404 = client.post(
        f"/api/v1/financial/quotes/{fake_id}/approve",
        json={"step_number": 1, "decision": "APPROVED"},
    )
    assert res_404.status_code == status.HTTP_404_NOT_FOUND

    # Test rejection
    acc_id = str(uuid.uuid4())
    app1 = str(uuid.uuid4())
    q = create_quote(test_db, acc_id, "Rejection Test", 25000, approver_ids=[app1])

    res_reject = client.post(
        f"/api/v1/financial/quotes/{q.id}/approve",
        json={
            "step_number": 1,
            "decision": "REJECTED",
            "comment": "Price too high.",
            "approver_id": app1,
        },
    )
    assert res_reject.status_code == status.HTTP_200_OK
    data = res_reject.json()
    assert data["status"] == "REJECTED"
    assert data["approval_steps"][0]["status"] == "REJECTED"


def test_delete_quote_endpoint(client: TestClient, test_db: DBSession):
    """Verify DELETE /api/v1/financial/quotes/{quote_id} removes quote record."""
    acc_id = str(uuid.uuid4())
    q = create_quote(test_db, acc_id, "To Delete", 5000)

    del_res = client.delete(f"/api/v1/financial/quotes/{q.id}")
    assert del_res.status_code == status.HTTP_204_NO_CONTENT
    assert del_res.content == b""

    # Verify 404 on subsequent get
    get_res = client.get(f"/api/v1/financial/quotes/{q.id}")
    assert get_res.status_code == status.HTTP_404_NOT_FOUND
