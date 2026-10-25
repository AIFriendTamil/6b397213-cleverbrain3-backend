"""Integration tests for AR aging and ARR trend dashboard endpoints in backend/src/api/dashboard_routes.py."""

import uuid
from datetime import date as date_type, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.financial import ArAgingSnapshot, ArrMetric
from src.models.invoice import Invoice, PaymentStatus


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


def test_get_ar_aging_endpoint_empty(client: TestClient):
    """Verify GET /api/v1/financial/ar-aging returns zeroes when no invoices exist."""
    response = client.get("/api/v1/financial/ar-aging")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["account_id"] == "ALL"
    assert data["current_balance"] == 0.0
    assert data["bucket_30_balance"] == 0.0
    assert data["bucket_60_balance"] == 0.0
    assert data["bucket_90_plus_balance"] == 0.0
    assert data["total_outstanding"] == 0.0
    assert "as_of_timestamp" in data


def test_get_ar_aging_endpoint_with_invoices(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/financial/ar-aging categorizes unpaid invoices into buckets."""
    account_id = str(uuid.uuid4())
    today = datetime.now(timezone.utc).date()

    # Current invoice (due in 10 days)
    inv_curr = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-001",
        issue_date=today - timedelta(days=10),
        due_date=today + timedelta(days=10),
        amount=Decimal("1500.00"),
        payment_status=PaymentStatus.UNPAID,
    )
    # 15 days overdue (30-day bucket)
    inv_30 = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-002",
        issue_date=today - timedelta(days=45),
        due_date=today - timedelta(days=15),
        amount=Decimal("2500.00"),
        payment_status=PaymentStatus.OVERDUE,
    )
    # 45 days overdue (60-day bucket)
    inv_60 = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-003",
        issue_date=today - timedelta(days=75),
        due_date=today - timedelta(days=45),
        amount=Decimal("3500.00"),
        payment_status=PaymentStatus.OVERDUE,
    )
    # 90 days overdue (90+ day bucket)
    inv_90 = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-004",
        issue_date=today - timedelta(days=120),
        due_date=today - timedelta(days=90),
        amount=Decimal("4500.00"),
        payment_status=PaymentStatus.OVERDUE,
    )
    # Paid invoice (should be excluded)
    inv_paid = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-005",
        issue_date=today - timedelta(days=60),
        due_date=today - timedelta(days=30),
        amount=Decimal("10000.00"),
        payment_status=PaymentStatus.PAID,
    )

    test_db.add_all([inv_curr, inv_30, inv_60, inv_90, inv_paid])
    test_db.commit()

    response = client.get(f"/api/v1/financial/ar-aging?account_id={account_id}&force_recalculate=true")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["account_id"] == account_id
    assert data["current_balance"] == 1500.0
    assert data["bucket_30_balance"] == 2500.0
    assert data["bucket_60_balance"] == 3500.0
    assert data["bucket_90_plus_balance"] == 4500.0
    assert data["total_outstanding"] == 12000.0


def test_get_arr_trends_endpoint(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/financial/arr-trends returns trend data points and converts currencies."""
    account_id = str(uuid.uuid4())

    metric1 = ArrMetric(
        account_id=account_id,
        metric_period="2026-Q1",
        arr_value=Decimal("100000.00"),
        currency="USD",
        recorded_at=datetime.now(timezone.utc) - timedelta(days=90),
    )
    metric2 = ArrMetric(
        account_id=account_id,
        metric_period="2026-Q2",
        arr_value=Decimal("100000.00"),
        currency="EUR",  # EUR -> USD rate is 1.08 => 108,000.00 USD
        recorded_at=datetime.now(timezone.utc),
    )

    test_db.add_all([metric1, metric2])
    test_db.commit()

    response = client.get(f"/api/v1/financial/arr-trends?account_id={account_id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2
    assert data[0]["metric_period"] == "2026-Q1"
    assert data[0]["arr_value"] == 100000.0
    assert data[0]["currency"] == "USD"

    assert data[1]["metric_period"] == "2026-Q2"
    assert data[1]["arr_value"] == 108000.0
    assert data[1]["currency"] == "USD"
    assert data[1]["original_currency"] == "EUR"


def test_get_arr_trends_filter_by_period(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/financial/arr-trends filters by period_range."""
    account_id = str(uuid.uuid4())

    metric1 = ArrMetric(
        account_id=account_id,
        metric_period="2025-Q4",
        arr_value=Decimal("80000.00"),
        currency="USD",
    )
    metric2 = ArrMetric(
        account_id=account_id,
        metric_period="2026-Q1",
        arr_value=Decimal("95000.00"),
        currency="USD",
    )

    test_db.add_all([metric1, metric2])
    test_db.commit()

    response = client.get(f"/api/v1/financial/arr-trends?account_id={account_id}&period_range=2026-Q1")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["metric_period"] == "2026-Q1"
    assert data[0]["arr_value"] == 95000.0


def test_get_financial_dashboard_overview_endpoint(client: TestClient, test_db: DBSession):
    """Verify GET /api/v1/financial/dashboard returns composite dashboard data."""
    account_id = str(uuid.uuid4())

    metric = ArrMetric(
        account_id=account_id,
        metric_period="2026-Q1",
        arr_value=Decimal("150000.00"),
        currency="USD",
    )
    test_db.add(metric)
    test_db.commit()

    response = client.get(f"/api/v1/financial/dashboard?account_id={account_id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["account_id"] == account_id
    assert "ar_aging" in data
    assert "arr_trends" in data
    assert len(data["arr_trends"]) == 1
    assert data["total_arr"] == 150000.0
