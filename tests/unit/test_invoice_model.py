"""Unit tests for Invoice ORM entity model."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.invoice import Invoice, PaymentStatus


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_invoice_creation_and_defaults(test_session: Session):
    """Verify Invoice creation, default payment status, UUID generation, amount, and timestamps."""
    account_id = str(uuid.uuid4())
    issue_date = date(2026, 1, 15)
    due_date = date(2026, 2, 15)

    invoice = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-001",
        issue_date=issue_date,
        due_date=due_date,
        amount=Decimal("12500.50"),
    )
    test_session.add(invoice)
    test_session.commit()

    retrieved = test_session.query(Invoice).filter_by(account_id=account_id).first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.account_id == account_id
    assert retrieved.invoice_number == "INV-2026-001"
    assert retrieved.issue_date == issue_date
    assert retrieved.due_date == due_date
    assert retrieved.amount == Decimal("12500.50")
    assert retrieved.payment_status == PaymentStatus.UNPAID
    assert isinstance(retrieved.created_at, datetime)


def test_invoice_payment_status_variations(test_session: Session):
    """Verify storing different payment statuses on Invoice records."""
    account_id = str(uuid.uuid4())
    today = date(2026, 3, 1)

    inv_paid = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-002",
        issue_date=today,
        due_date=today,
        amount=Decimal("500.00"),
        payment_status=PaymentStatus.PAID,
    )
    inv_overdue = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-003",
        issue_date=today,
        due_date=today,
        amount=Decimal("1500.00"),
        payment_status=PaymentStatus.OVERDUE,
    )
    inv_partial = Invoice(
        account_id=account_id,
        invoice_number="INV-2026-004",
        issue_date=today,
        due_date=today,
        amount=Decimal("3000.00"),
        payment_status=PaymentStatus.PARTIALLY_PAID,
    )

    test_session.add_all([inv_paid, inv_overdue, inv_partial])
    test_session.commit()

    overdue_invoices = (
        test_session.query(Invoice)
        .filter_by(account_id=account_id, payment_status=PaymentStatus.OVERDUE)
        .all()
    )
    assert len(overdue_invoices) == 1
    assert overdue_invoices[0].invoice_number == "INV-2026-003"


def test_invoice_number_unique_constraint(test_session: Session):
    """Verify that creating two invoices with the same invoice_number raises an IntegrityError."""
    account_id = str(uuid.uuid4())
    today = date(2026, 3, 1)

    inv1 = Invoice(
        account_id=account_id,
        invoice_number="INV-DUPLICATE",
        issue_date=today,
        due_date=today,
        amount=Decimal("100.00"),
    )
    inv2 = Invoice(
        account_id=account_id,
        invoice_number="INV-DUPLICATE",
        issue_date=today,
        due_date=today,
        amount=Decimal("200.00"),
    )

    test_session.add(inv1)
    test_session.commit()

    test_session.add(inv2)
    with pytest.raises(IntegrityError):
        test_session.commit()
