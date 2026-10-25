"""Unit tests for AR aging calculation and ARR trend aggregation logic in backend/src/services/financial/dashboard.py."""

import uuid
from datetime import date as date_type, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.financial import ArAgingSnapshot, ArrMetric
from src.models.invoice import Invoice, PaymentStatus
from src.services.financial.dashboard import (
    ArAgingBucketSummary,
    ArrTrendDataPoint,
    calculate_ar_aging,
    convert_currency_amount,
    get_ar_aging_summary,
    get_arr_trends,
    get_financial_dashboard_data,
    record_arr_metric,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_convert_currency_amount():
    """Verify currency conversion calculations and fallback handling."""
    assert convert_currency_amount(100.0, "USD", "USD") == 100.0
    assert convert_currency_amount(100.0, "EUR", "USD") == 108.0
    assert convert_currency_amount(100.0, "GBP", "USD") == 127.0
    assert convert_currency_amount(None, "USD", "USD") == 0.0
    # Unknown currency falls back to original amount
    assert convert_currency_amount(50.0, "UNKNOWN", "USD") == 50.0


def test_calculate_ar_aging_with_various_buckets(test_session: Session):
    """Verify AR aging calculation across current, 30, 60, and 90+ day buckets."""
    account_id = str(uuid.uuid4())
    ref_date = date_type(2026, 3, 1)

    # 1. Current invoice (due today or in future)
    inv_current = Invoice(
        account_id=account_id,
        invoice_number="INV-001",
        issue_date=date_type(2026, 2, 15),
        due_date=date_type(2026, 3, 15),
        amount=Decimal("1000.00"),
        payment_status=PaymentStatus.UNPAID,
    )
    # 2. 15 days overdue (1-30 day bucket)
    inv_30 = Invoice(
        account_id=account_id,
        invoice_number="INV-002",
        issue_date=date_type(2026, 1, 15),
        due_date=date_type(2026, 2, 14),
        amount=Decimal("2000.00"),
        payment_status=PaymentStatus.OVERDUE,
    )
    # 3. 45 days overdue (31-60 day bucket)
    inv_60 = Invoice(
        account_id=account_id,
        invoice_number="INV-003",
        issue_date=date_type(2025, 12, 15),
        due_date=date_type(2026, 1, 15),
        amount=Decimal("3000.00"),
        payment_status=PaymentStatus.PARTIALLY_PAID,
    )
    # 4. 75 days overdue (90+ day bucket)
    inv_90 = Invoice(
        account_id=account_id,
        invoice_number="INV-004",
        issue_date=date_type(2025, 11, 15),
        due_date=date_type(2025, 12, 16),
        amount=Decimal("4000.00"),
        payment_status=PaymentStatus.OVERDUE,
    )
    # 5. Paid invoice (should be excluded from balance calculation)
    inv_paid = Invoice(
        account_id=account_id,
        invoice_number="INV-005",
        issue_date=date_type(2025, 11, 15),
        due_date=date_type(2025, 12, 15),
        amount=Decimal("5000.00"),
        payment_status=PaymentStatus.PAID,
    )

    test_session.add_all([inv_current, inv_30, inv_60, inv_90, inv_paid])
    test_session.commit()

    snapshot = calculate_ar_aging(
        test_session,
        account_id=account_id,
        as_of_date=ref_date,
        save_snapshot=True,
    )

    assert snapshot.account_id == account_id
    assert snapshot.current_balance == Decimal("1000.00")
    assert snapshot.bucket_30_balance == Decimal("2000.00")
    assert snapshot.bucket_60_balance == Decimal("3000.00")
    assert snapshot.bucket_90_plus_balance == Decimal("4000.00")


def test_get_ar_aging_summary_and_force_recalculate(test_session: Session):
    """Verify retrieving AR aging summary and forcing dynamic recalculation."""
    account_id = str(uuid.uuid4())

    summary = get_ar_aging_summary(test_session, account_id=account_id)
    assert isinstance(summary, ArAgingBucketSummary)
    assert summary.account_id == account_id
    assert summary.total_outstanding == 0.0

    # Add an invoice and force recalculation
    inv = Invoice(
        account_id=account_id,
        invoice_number="INV-100",
        issue_date=date_type(2026, 1, 1),
        due_date=date_type(2026, 1, 15),
        amount=Decimal("1500.00"),
        payment_status=PaymentStatus.UNPAID,
    )
    test_session.add(inv)
    test_session.commit()

    recalculated_summary = get_ar_aging_summary(
        test_session,
        account_id=account_id,
        force_recalculate=True,
    )
    assert recalculated_summary.total_outstanding == 1500.0


def test_record_and_get_arr_trends(test_session: Session):
    """Verify recording ARR metrics and querying trend data with currency conversion."""
    account_id = str(uuid.uuid4())

    record_arr_metric(
        test_session,
        account_id=account_id,
        metric_period="2025-Q3",
        arr_value=Decimal("100000.00"),
        currency="USD",
    )
    record_arr_metric(
        test_session,
        account_id=account_id,
        metric_period="2025-Q4",
        arr_value=Decimal("100000.00"),
        currency="EUR",
    )

    trends = get_arr_trends(
        test_session,
        account_id=account_id,
        target_currency="USD",
    )

    assert len(trends) == 2
    assert trends[0].metric_period == "2025-Q3"
    assert trends[0].arr_value == 100000.0
    assert trends[1].metric_period == "2025-Q4"
    assert trends[1].arr_value == 108000.0  # 100000 * 1.08 EUR->USD conversion


def test_get_arr_trends_period_filter(test_session: Session):
    """Verify filtering ARR trends by period range string."""
    account_id = str(uuid.uuid4())

    record_arr_metric(test_session, account_id, "2025-Q1", 50000)
    record_arr_metric(test_session, account_id, "2025-Q2", 60000)
    record_arr_metric(test_session, account_id, "2025-Q3", 70000)

    filtered = get_arr_trends(
        test_session,
        account_id=account_id,
        period_range="2025-Q1, 2025-Q3",
    )
    assert len(filtered) == 2
    periods = [p.metric_period for p in filtered]
    assert "2025-Q1" in periods
    assert "2025-Q3" in periods
    assert "2025-Q2" not in periods


def test_get_financial_dashboard_data(test_session: Session):
    """Verify aggregate financial dashboard data generation."""
    account_id = str(uuid.uuid4())

    record_arr_metric(test_session, account_id, "2026-Q1", 120000)

    data = get_financial_dashboard_data(test_session, account_id=account_id)
    assert data["account_id"] == account_id
    assert "ar_aging" in data
    assert "arr_trends" in data
    assert data["total_arr"] == 120000.0
    assert "calculated_at" in data
