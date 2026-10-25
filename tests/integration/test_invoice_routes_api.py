"""Integration tests for invoice list tracking, filtering, and detail API endpoints in backend/src/api/invoice_routes.py."""

import uuid
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.invoice import PaymentStatus
from src.services.financial.invoice import create_invoice


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


def test_list_invoices_endpoint_empty(client: TestClient):
    """Verify GET /api/v1/financial/invoices returns an empty list when no invoices exist."""
    response = client.get("/api/v1/financial/invoices")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_create_and_get_invoice_endpoints(client: TestClient):
    """Verify POST /api/v1/financial/invoices creates an invoice and GET retrieves it."""
    account_id = str(uuid.uuid4())
    payload = {
        "account_id": account_id,
        "invoice_number": "INV-2026-001",
        "issue_date": "2026-03-01",
        "due_date": "2026-03-31",
        "amount": 15000.0,
        "payment_status": "UNPAID",
    }

    create_res = client.post("/api/v1/financial/invoices", json=payload)
    assert create_res.status_code == status.HTTP_201_CREATED
    data = create_res.json()

    invoice_id = data["id"]
    assert data["account_id"] == account_id
    assert data["invoice_number"] == "INV-2026-001"
    assert data["issue_date"] == "2026-03-01"
    assert data["due_date"] == "2026-03-31"
    assert data["amount"] == 15000.0
    assert data["payment_status"] == "UNPAID"

    # Retrieve invoice by ID
    get_res = client.get(f"/api/v1/financial/invoices/{invoice_id}")
    assert get_res.status_code == status.HTTP_200_OK
    get_data = get_res.json()
    assert get_data["id"] == invoice_id
    assert get_data["invoice_number"] == "INV-2026-001"


def test_create_duplicate_invoice_number_conflict(client: TestClient):
    """Verify POST /api/v1/financial/invoices returns HTTP 409 Conflict when invoice_number exists."""
    account_id = str(uuid.uuid4())
    payload = {
        "account_id": account_id,
        "invoice_number": "INV-2026-DUP",
        "issue_date": "2026-03-01",
        "due_date": "2026-03-31",
        "amount": 5000.0,
    }

    res1 = client.post("/api/v1/financial/invoices", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post("/api/v1/financial/invoices", json=payload)
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert res2.json()["detail"]["code"] == "INVOICE_CONFLICT"


def test_list_invoices_filtering(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/financial/invoices filtering by account_id and payment_status."""
    acc_id_1 = str(uuid.uuid4())
    acc_id_2 = str(uuid.uuid4())

    create_invoice(
        test_db, acc_id_1, "INV-101", "2026-01-01", "2026-01-31", 1000, PaymentStatus.UNPAID
    )
    create_invoice(
        test_db, acc_id_1, "INV-102", "2026-02-01", "2026-02-28", 2000, PaymentStatus.PAID
    )
    create_invoice(
        test_db, acc_id_2, "INV-103", "2026-03-01", "2026-03-31", 3000, PaymentStatus.OVERDUE
    )

    # Filter by account_id
    res_acc1 = client.get(f"/api/v1/financial/invoices?account_id={acc_id_1}")
    assert res_acc1.status_code == status.HTTP_200_OK
    data_acc1 = res_acc1.json()
    assert len(data_acc1) == 2

    # Filter by payment_status
    res_paid = client.get("/api/v1/financial/invoices?payment_status=PAID")
    assert res_paid.status_code == status.HTTP_200_OK
    data_paid = res_paid.json()
    assert len(data_paid) == 1
    assert data_paid[0]["invoice_number"] == "INV-102"

    # Filter by both account_id and payment_status
    res_both = client.get(
        f"/api/v1/financial/invoices?account_id={acc_id_1}&payment_status=UNPAID"
    )
    assert res_both.status_code == status.HTTP_200_OK
    data_both = res_both.json()
    assert len(data_both) == 1
    assert data_both[0]["invoice_number"] == "INV-101"


def test_update_invoice_status_endpoint(client: TestClient, test_db: DBSession):
    """Verify PATCH /api/v1/financial/invoices/{id}/status updates payment status."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(test_db, acc_id, "INV-UPD-01", "2026-01-01", "2026-01-31", 5000.0)

    patch_res = client.patch(
        f"/api/v1/financial/invoices/{inv.id}/status",
        json={"payment_status": "PAID"},
    )
    assert patch_res.status_code == status.HTTP_200_OK
    data = patch_res.json()
    assert data["payment_status"] == "PAID"

    # Invalid status
    bad_res = client.patch(
        f"/api/v1/financial/invoices/{inv.id}/status",
        json={"payment_status": "INVALID_STATUS"},
    )
    assert bad_res.status_code == status.HTTP_400_BAD_REQUEST


def test_update_invoice_endpoint(client: TestClient, test_db: DBSession):
    """Verify PATCH /api/v1/financial/invoices/{id} updates invoice amount and dates."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(test_db, acc_id, "INV-UPD-02", "2026-01-01", "2026-01-31", 5000.0)

    patch_res = client.patch(
        f"/api/v1/financial/invoices/{inv.id}",
        json={"amount": 7500.0, "payment_status": "PARTIALLY_PAID"},
    )
    assert patch_res.status_code == status.HTTP_200_OK
    data = patch_res.json()
    assert data["amount"] == 7500.0
    assert data["payment_status"] == "PARTIALLY_PAID"


def test_delete_invoice_endpoint(client: TestClient, test_db: DBSession):
    """Verify DELETE /api/v1/financial/invoices/{id} deletes the invoice and returns 204."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(test_db, acc_id, "INV-DEL-01", "2026-01-01", "2026-01-31", 1000.0)

    del_res = client.delete(f"/api/v1/financial/invoices/{inv.id}")
    assert del_res.status_code == status.HTTP_204_NO_CONTENT
    assert del_res.content == b""

    get_res = client.get(f"/api/v1/financial/invoices/{inv.id}")
    assert get_res.status_code == status.HTTP_404_NOT_FOUND
