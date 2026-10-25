"""Unit tests for Quote and QuoteApproval ORM entity models."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.quote import Quote, QuoteApproval, QuoteApprovalStatus, QuoteStatus


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_quote_creation_and_defaults(test_session: Session):
    """Verify Quote creation, default status, UUID generation, total_amount, and timestamps."""
    account_id = str(uuid.uuid4())
    quote = Quote(
        account_id=account_id,
        title="Software Enterprise License Quote",
        total_amount=Decimal("25000.00"),
    )
    test_session.add(quote)
    test_session.commit()

    retrieved = test_session.query(Quote).filter_by(account_id=account_id).first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.title == "Software Enterprise License Quote"
    assert retrieved.status == QuoteStatus.DRAFT
    assert retrieved.total_amount == Decimal("25000.00")
    assert isinstance(retrieved.created_at, datetime)
    assert isinstance(retrieved.updated_at, datetime)


def test_quote_and_approval_relationships(test_session: Session):
    """Verify Quote and QuoteApproval relationship and status transitions."""
    quote = Quote(
        account_id=str(uuid.uuid4()),
        title="Multi-Step Approval Quote",
        status=QuoteStatus.PENDING_APPROVAL,
        total_amount=Decimal("100000.00"),
    )

    approver1_id = str(uuid.uuid4())
    approver2_id = str(uuid.uuid4())

    approval_step1 = QuoteApproval(
        approver_id=approver1_id,
        step_number=1,
        status=QuoteApprovalStatus.APPROVED,
        comment="Approved by Manager",
        decided_at=datetime.now(timezone.utc),
    )
    approval_step2 = QuoteApproval(
        approver_id=approver2_id,
        step_number=2,
        status=QuoteApprovalStatus.PENDING,
    )

    quote.approvals.append(approval_step1)
    quote.approvals.append(approval_step2)

    test_session.add(quote)
    test_session.commit()

    retrieved_quote = test_session.query(Quote).filter_by(id=quote.id).first()
    assert retrieved_quote is not None
    assert len(retrieved_quote.approvals) == 2
    assert retrieved_quote.approvals[0].step_number == 1
    assert retrieved_quote.approvals[0].status == QuoteApprovalStatus.APPROVED
    assert retrieved_quote.approvals[0].comment == "Approved by Manager"
    assert retrieved_quote.approvals[0].quote.id == quote.id

    assert retrieved_quote.approvals[1].step_number == 2
    assert retrieved_quote.approvals[1].status == QuoteApprovalStatus.PENDING
    assert retrieved_quote.approvals[1].decided_at is None


def test_quote_cascade_delete(test_session: Session):
    """Verify that deleting a Quote cascades and deletes its associated QuoteApproval records."""
    quote = Quote(
        account_id=str(uuid.uuid4()),
        title="Quote to be deleted",
        status=QuoteStatus.DRAFT,
        total_amount=Decimal("5000.00"),
    )
    approval = QuoteApproval(
        approver_id=str(uuid.uuid4()),
        step_number=1,
        status=QuoteApprovalStatus.PENDING,
    )
    quote.approvals.append(approval)

    test_session.add(quote)
    test_session.commit()

    quote_id = quote.id
    approval_id = approval.id

    # Confirm approval exists
    assert test_session.query(QuoteApproval).filter_by(id=approval_id).first() is not None

    # Delete quote
    test_session.delete(quote)
    test_session.commit()

    # Confirm quote and approval are deleted
    assert test_session.query(Quote).filter_by(id=quote_id).first() is None
    assert test_session.query(QuoteApproval).filter_by(id=approval_id).first() is None
