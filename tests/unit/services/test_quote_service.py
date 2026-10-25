"""Unit tests for quote domain service operations and approval workflow transitions."""

import uuid
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.quote import Quote, QuoteApprovalStatus, QuoteStatus
from src.services.financial.quote import (
    QuoteConflictError,
    QuoteNotFoundError,
    QuoteValidationError,
    create_quote,
    delete_quote,
    get_quote_by_id,
    get_quotes,
    process_quote_approval,
    submit_quote_for_approval,
    update_quote,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_create_quote_success(test_session: Session):
    """Verify creating a quote in DRAFT status without approval steps."""
    account_id = str(uuid.uuid4())
    quote = create_quote(
        db=test_session,
        account_id=account_id,
        title="Enterprise License Agreement",
        total_amount=Decimal("50000.00"),
    )

    assert quote.id is not None
    assert quote.account_id == account_id
    assert quote.title == "Enterprise License Agreement"
    assert quote.total_amount == Decimal("50000.00")
    assert quote.status == QuoteStatus.DRAFT
    assert len(quote.approvals) == 0


def test_create_quote_with_approvers(test_session: Session):
    """Verify creating a quote with approvers sets status to PENDING_APPROVAL and builds steps."""
    account_id = str(uuid.uuid4())
    approver1 = str(uuid.uuid4())
    approver2 = str(uuid.uuid4())

    quote = create_quote(
        db=test_session,
        account_id=account_id,
        title="Multi-Level Sales Deal",
        total_amount=120000.0,
        approver_ids=[approver1, approver2],
    )

    assert quote.status == QuoteStatus.PENDING_APPROVAL
    assert len(quote.approvals) == 2
    assert quote.approvals[0].step_number == 1
    assert quote.approvals[0].approver_id == approver1
    assert quote.approvals[0].status == QuoteApprovalStatus.PENDING
    assert quote.approvals[1].step_number == 2
    assert quote.approvals[1].approver_id == approver2


def test_create_quote_validation_errors(test_session: Session):
    """Verify validation errors when creating quote with empty required fields or negative amount."""
    with pytest.raises(QuoteValidationError, match="Account ID must be provided"):
        create_quote(test_session, "", "Title", 100)

    with pytest.raises(QuoteValidationError, match="Quote title must be provided"):
        create_quote(test_session, str(uuid.uuid4()), "  ", 100)

    with pytest.raises(QuoteValidationError, match="Total amount cannot be negative"):
        create_quote(test_session, str(uuid.uuid4()), "Title", -50)


def test_get_quotes_and_get_by_id(test_session: Session):
    """Verify querying quotes by ID and filtering by account or status."""
    acc_id_1 = str(uuid.uuid4())
    acc_id_2 = str(uuid.uuid4())

    q1 = create_quote(test_session, acc_id_1, "Quote 1", 1000)
    q2 = create_quote(test_session, acc_id_1, "Quote 2", 2000, approver_ids=[str(uuid.uuid4())])
    q3 = create_quote(test_session, acc_id_2, "Quote 3", 3000)

    retrieved_q1 = get_quote_by_id(test_session, q1.id)
    assert retrieved_q1 is not None
    assert retrieved_q1.title == "Quote 1"

    assert get_quote_by_id(test_session, str(uuid.uuid4())) is None

    # Filter by account_id
    acc1_quotes = get_quotes(test_session, account_id=acc_id_1)
    assert len(acc1_quotes) == 2

    # Filter by status
    draft_quotes = get_quotes(test_session, status=QuoteStatus.DRAFT)
    assert len(draft_quotes) == 2

    pending_quotes = get_quotes(test_session, status="PENDING_APPROVAL")
    assert len(pending_quotes) == 1
    assert pending_quotes[0].id == q2.id


def test_submit_quote_for_approval(test_session: Session):
    """Verify submitting a DRAFT quote for approval creates sequential steps."""
    quote = create_quote(test_session, str(uuid.uuid4()), "Draft Quote", 15000)
    assert quote.status == QuoteStatus.DRAFT

    app_id_1 = str(uuid.uuid4())
    app_id_2 = str(uuid.uuid4())

    submitted = submit_quote_for_approval(
        test_session,
        quote_id=quote.id,
        approver_ids=[app_id_1, app_id_2],
    )

    assert submitted.status == QuoteStatus.PENDING_APPROVAL
    assert len(submitted.approvals) == 2
    assert submitted.approvals[0].step_number == 1
    assert submitted.approvals[0].approver_id == app_id_1
    assert submitted.approvals[1].step_number == 2
    assert submitted.approvals[1].approver_id == app_id_2


def test_submit_quote_errors(test_session: Session):
    """Verify error conditions for submit_quote_for_approval."""
    with pytest.raises(QuoteValidationError, match="At least one approver ID must be specified"):
        submit_quote_for_approval(test_session, str(uuid.uuid4()), [])

    with pytest.raises(QuoteNotFoundError):
        submit_quote_for_approval(test_session, str(uuid.uuid4()), [str(uuid.uuid4())])

    quote = create_quote(
        test_session,
        str(uuid.uuid4()),
        "Already Pending",
        5000,
        approver_ids=[str(uuid.uuid4())],
    )
    with pytest.raises(QuoteConflictError, match="expected 'DRAFT'"):
        submit_quote_for_approval(test_session, quote.id, [str(uuid.uuid4())])


def test_process_quote_approval_multi_step_flow(test_session: Session):
    """Verify full multi-step approval workflow leading to APPROVED status."""
    approver1 = str(uuid.uuid4())
    approver2 = str(uuid.uuid4())
    quote = create_quote(
        test_session,
        account_id=str(uuid.uuid4()),
        title="2-Step Big Contract",
        total_amount=250000.0,
        approver_ids=[approver1, approver2],
    )
    assert quote.status == QuoteStatus.PENDING_APPROVAL

    # Step 1 Approved
    partially_approved = process_quote_approval(
        db=test_session,
        quote_id=quote.id,
        step_number=1,
        decision="APPROVED",
        approver_id=approver1,
        comment="Step 1 looks good",
    )
    assert partially_approved.status == QuoteStatus.PENDING_APPROVAL
    assert partially_approved.approvals[0].status == QuoteApprovalStatus.APPROVED
    assert partially_approved.approvals[0].comment == "Step 1 looks good"
    assert partially_approved.approvals[0].decided_at is not None

    # Step 2 Approved -> Quote transitions to APPROVED
    fully_approved = process_quote_approval(
        db=test_session,
        quote_id=quote.id,
        step_number=2,
        decision=QuoteApprovalStatus.APPROVED,
        approver_id=approver2,
        comment="Final approval granted",
    )
    assert fully_approved.status == QuoteStatus.APPROVED
    assert fully_approved.approvals[1].status == QuoteApprovalStatus.APPROVED


def test_process_quote_approval_rejection_flow(test_session: Session):
    """Verify quote rejection at step 1 immediately transitions quote to REJECTED."""
    approver1 = str(uuid.uuid4())
    approver2 = str(uuid.uuid4())
    quote = create_quote(
        test_session,
        account_id=str(uuid.uuid4()),
        title="2-Step Contract Rejected",
        total_amount=75000.0,
        approver_ids=[approver1, approver2],
    )

    rejected_quote = process_quote_approval(
        db=test_session,
        quote_id=quote.id,
        step_number=1,
        decision="REJECTED",
        approver_id=approver1,
        comment="Budget exceeded",
    )

    assert rejected_quote.status == QuoteStatus.REJECTED
    assert rejected_quote.approvals[0].status == QuoteApprovalStatus.REJECTED
    assert rejected_quote.approvals[1].status == QuoteApprovalStatus.PENDING


def test_process_quote_approval_out_of_order_conflict(test_session: Session):
    """Verify attempting to approve step 2 before step 1 raises QuoteConflictError."""
    approver1 = str(uuid.uuid4())
    approver2 = str(uuid.uuid4())
    quote = create_quote(
        test_session,
        account_id=str(uuid.uuid4()),
        title="Out of Order Test",
        total_amount=50000.0,
        approver_ids=[approver1, approver2],
    )

    with pytest.raises(QuoteConflictError, match="Out-of-order approval submission"):
        process_quote_approval(
            db=test_session,
            quote_id=quote.id,
            step_number=2,
            decision="APPROVED",
            approver_id=approver2,
        )


def test_process_quote_approval_already_decided_step(test_session: Session):
    """Verify attempting to approve an already decided step raises QuoteConflictError."""
    approver1 = str(uuid.uuid4())
    quote = create_quote(
        test_session,
        account_id=str(uuid.uuid4()),
        title="Double Decide Test",
        total_amount=10000.0,
        approver_ids=[approver1],
    )

    process_quote_approval(test_session, quote.id, step_number=1, decision="APPROVED")

    with pytest.raises(QuoteConflictError, match="expected 'PENDING_APPROVAL'"):
        process_quote_approval(test_session, quote.id, step_number=1, decision="APPROVED")


def test_process_quote_approval_unauthorized_approver(test_session: Session):
    """Verify providing a non-matching approver_id raises QuoteConflictError."""
    approver1 = str(uuid.uuid4())
    wrong_approver = str(uuid.uuid4())
    quote = create_quote(
        test_session,
        account_id=str(uuid.uuid4()),
        title="Auth Test",
        total_amount=10000.0,
        approver_ids=[approver1],
    )

    with pytest.raises(QuoteConflictError, match="is not authorized to approve step"):
        process_quote_approval(
            test_session,
            quote.id,
            step_number=1,
            decision="APPROVED",
            approver_id=wrong_approver,
        )


def test_update_and_delete_quote(test_session: Session):
    """Verify updating and deleting a DRAFT quote."""
    quote = create_quote(test_session, str(uuid.uuid4()), "Initial Title", 5000)

    updated = update_quote(test_session, quote.id, title="Updated Title", total_amount=7500)
    assert updated.title == "Updated Title"
    assert updated.total_amount == Decimal("7500.00")

    delete_quote(test_session, quote.id)
    assert get_quote_by_id(test_session, quote.id) is None

    with pytest.raises(QuoteNotFoundError):
        delete_quote(test_session, quote.id)
