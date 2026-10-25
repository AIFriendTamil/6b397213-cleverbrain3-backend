"""Unit tests for invoice domain service operations and payment status filtering."""

import uuid
from datetime import date as date_type
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.invoice import Invoice, PaymentStatus
from src.services.financial.invoice import (
    InvoiceConflictError,
    InvoiceNotFoundError,
    InvoiceValidationError,
    create_invoice,
    delete_invoice,
    get_invoice_by_id,
    get_invoice_by_id_or_raise,
    get_invoice_by_number,
    get_invoices,
    parse_payment_status,
    update_invoice,
    update_invoice_payment_status,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_parse_payment_status():
    """Verify payment status string and enum parsing and validation."""
    assert parse_payment_status(PaymentStatus.PAID) == PaymentStatus.PAID
    assert parse_payment_status("paid") == PaymentStatus.PAID
    assert parse_payment_status(" OVERDUE ") == PaymentStatus.OVERDUE
    assert parse_payment_status("PARTIALLY_PAID") == PaymentStatus.PARTIALLY_PAID

    with pytest.raises(InvoiceValidationError, match="Invalid payment status"):
        parse_payment_status("INVALID_STATUS")

    with pytest.raises(InvoiceValidationError, match="Payment status string cannot be empty"):
        parse_payment_status("")


def test_create_invoice_success(test_session: Session):
    """Verify creating an invoice record with valid parameters."""
    account_id = str(uuid.uuid4())
    issue_date = date_type(2026, 3, 1)
    due_date = date_type(2026, 3, 31)

    invoice = create_invoice(
        db=test_session,
        account_id=account_id,
        invoice_number="INV-2026-100",
        issue_date=issue_date,
        due_date=due_date,
        amount=Decimal("2500.00"),
    )

    assert invoice.id is not None
    assert invoice.account_id == account_id
    assert invoice.invoice_number == "INV-2026-100"
    assert invoice.issue_date == issue_date
    assert invoice.due_date == due_date
    assert invoice.amount == Decimal("2500.00")
    assert invoice.payment_status == PaymentStatus.UNPAID


def test_create_invoice_with_string_dates_and_status(test_session: Session):
    """Verify creating an invoice using string date formats and explicit payment status."""
    account_id = str(uuid.uuid4())

    invoice = create_invoice(
        db=test_session,
        account_id=account_id,
        invoice_number="INV-2026-101",
        issue_date="2026-04-01",
        due_date="2026-04-30",
        amount=5000.50,
        payment_status="PAID",
    )

    assert invoice.issue_date == date_type(2026, 4, 1)
    assert invoice.due_date == date_type(2026, 4, 30)
    assert invoice.amount == Decimal("5000.50")
    assert invoice.payment_status == PaymentStatus.PAID


def test_create_invoice_validation_errors(test_session: Session):
    """Verify validation errors for invalid account_id, invoice_number, amount, and dates."""
    acc_id = str(uuid.uuid4())

    with pytest.raises(InvoiceValidationError, match="Account ID must be provided"):
        create_invoice(test_session, "", "INV-102", "2026-01-01", "2026-01-31", 100)

    with pytest.raises(InvoiceValidationError, match="Invoice number must be provided"):
        create_invoice(test_session, acc_id, "   ", "2026-01-01", "2026-01-31", 100)

    with pytest.raises(InvoiceValidationError, match="Invoice amount cannot be negative"):
        create_invoice(test_session, acc_id, "INV-102", "2026-01-01", "2026-01-31", -10)

    with pytest.raises(InvoiceValidationError, match="Invalid monetary amount"):
        create_invoice(test_session, acc_id, "INV-102", "2026-01-01", "2026-01-31", "abc")

    with pytest.raises(InvoiceValidationError, match="Invalid date format for issue_date"):
        create_invoice(test_session, acc_id, "INV-102", "invalid-date", "2026-01-31", 100)


def test_create_invoice_conflict_error(test_session: Session):
    """Verify conflict error when attempting to create duplicate invoice_number."""
    acc_id = str(uuid.uuid4())

    create_invoice(
        db=test_session,
        account_id=acc_id,
        invoice_number="INV-2026-DUP",
        issue_date="2026-01-01",
        due_date="2026-01-31",
        amount=1000,
    )

    with pytest.raises(InvoiceConflictError, match="already exists"):
        create_invoice(
            db=test_session,
            account_id=acc_id,
            invoice_number="INV-2026-DUP",
            issue_date="2026-01-01",
            due_date="2026-01-31",
            amount=1000,
        )


def test_get_invoices_and_filtering(test_session: Session):
    """Verify retrieving invoices and filtering by account_id and payment_status."""
    acc1 = str(uuid.uuid4())
    acc2 = str(uuid.uuid4())

    inv1 = create_invoice(
        test_session, acc1, "INV-001", "2026-01-01", "2026-01-31", 100, PaymentStatus.UNPAID
    )
    inv2 = create_invoice(
        test_session, acc1, "INV-002", "2026-01-01", "2026-01-31", 200, PaymentStatus.PAID
    )
    inv3 = create_invoice(
        test_session, acc1, "INV-003", "2026-01-01", "2026-01-31", 300, PaymentStatus.OVERDUE
    )
    inv4 = create_invoice(
        test_session, acc2, "INV-004", "2026-01-01", "2026-01-31", 400, PaymentStatus.PAID
    )

    all_invoices = get_invoices(test_session)
    assert len(all_invoices) == 4

    acc1_invoices = get_invoices(test_session, account_id=acc1)
    assert len(acc1_invoices) == 3

    paid_invoices = get_invoices(test_session, payment_status=PaymentStatus.PAID)
    assert len(paid_invoices) == 2

    acc1_paid = get_invoices(test_session, account_id=acc1, payment_status="PAID")
    assert len(acc1_paid) == 1
    assert acc1_paid[0].id == inv2.id


def test_get_invoice_by_id_and_number(test_session: Session):
    """Verify retrieving single invoice by ID or invoice number."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(
        test_session, acc_id, "INV-BY-ID", "2026-01-01", "2026-01-31", 1500
    )

    found_by_id = get_invoice_by_id(test_session, inv.id)
    assert found_by_id is not None
    assert found_by_id.invoice_number == "INV-BY-ID"

    found_or_raised = get_invoice_by_id_or_raise(test_session, inv.id)
    assert found_or_raised.id == inv.id

    found_by_num = get_invoice_by_number(test_session, "INV-BY-ID")
    assert found_by_num is not None
    assert found_by_num.id == inv.id

    assert get_invoice_by_id(test_session, str(uuid.uuid4())) is None
    assert get_invoice_by_number(test_session, "NONEXISTENT") is None

    with pytest.raises(InvoiceNotFoundError):
        get_invoice_by_id_or_raise(test_session, str(uuid.uuid4()))


def test_update_invoice_payment_status(test_session: Session):
    """Verify updating payment status of an invoice."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(
        test_session, acc_id, "INV-UPDATE-STATUS", "2026-01-01", "2026-01-31", 2000, "UNPAID"
    )

    updated = update_invoice_payment_status(test_session, inv.id, "PAID")
    assert updated.payment_status == PaymentStatus.PAID

    with pytest.raises(InvoiceNotFoundError):
        update_invoice_payment_status(test_session, str(uuid.uuid4()), "PAID")


def test_update_invoice_fields(test_session: Session):
    """Verify updating fields on an existing invoice."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(
        test_session, acc_id, "INV-UPDATE-FIELDS", "2026-01-01", "2026-01-31", 1000, "UNPAID"
    )

    updated = update_invoice(
        db=test_session,
        invoice_id=inv.id,
        amount=1500.75,
        payment_status="PARTIALLY_PAID",
        issue_date="2026-02-01",
        due_date="2026-02-28",
    )

    assert updated.amount == Decimal("1500.75")
    assert updated.payment_status == PaymentStatus.PARTIALLY_PAID
    assert updated.issue_date == date_type(2026, 2, 1)
    assert updated.due_date == date_type(2026, 2, 28)


def test_delete_invoice(test_session: Session):
    """Verify deleting an invoice record."""
    acc_id = str(uuid.uuid4())
    inv = create_invoice(
        test_session, acc_id, "INV-DELETE", "2026-01-01", "2026-01-31", 500
    )

    assert delete_invoice(test_session, inv.id) is True
    assert get_invoice_by_id(test_session, inv.id) is None

    with pytest.raises(InvoiceNotFoundError):
        delete_invoice(test_session, inv.id)
