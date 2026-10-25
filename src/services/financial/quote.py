"""Domain service for quote lifecycle management and concurrency-locked multi-step approvals."""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import List, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.quote import Quote, QuoteApproval, QuoteApprovalStatus, QuoteStatus

logger = logging.getLogger(__name__)


class QuoteServiceError(Exception):
    """Base exception class for quote domain service operations."""

    pass


class QuoteNotFoundError(QuoteServiceError):
    """Raised when a requested quote or approval step cannot be found."""

    pass


class QuoteValidationError(QuoteServiceError):
    """Raised when quote inputs or parameters fail validation."""

    pass


class QuoteConflictError(QuoteServiceError):
    """Raised when quote state or step approval conflicts with workflow rules or sequence."""

    pass


def get_quote_by_id(db: Session, quote_id: str) -> Optional[Quote]:
    """Retrieve a Quote entity by ID with associated approval steps.

    Args:
        db: Synchronous SQLAlchemy database session.
        quote_id: UUID string of the target quote.

    Returns:
        Quote model instance if found, otherwise None.
    """
    stmt = select(Quote).where(Quote.id == quote_id)
    return db.scalars(stmt).first()


def get_quotes(
    db: Session,
    account_id: Optional[str] = None,
    status: Optional[Union[QuoteStatus, str]] = None,
) -> List[Quote]:
    """Retrieve quotes optionally filtered by account ID and lifecycle status.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Optional UUID string of account to filter quotes.
        status: Optional quote status filter (QuoteStatus enum or string).

    Returns:
        List of Quote model instances sorted by created_at descending.
    """
    stmt = select(Quote)
    if account_id:
        stmt = stmt.where(Quote.account_id == account_id)
    if status:
        status_enum = QuoteStatus(status) if isinstance(status, str) else status
        stmt = stmt.where(Quote.status == status_enum)
    stmt = stmt.order_by(Quote.created_at.desc())
    return list(db.scalars(stmt).all())


def create_quote(
    db: Session,
    account_id: str,
    title: str,
    total_amount: Union[Decimal, float, int],
    approver_ids: Optional[List[str]] = None,
    status: Optional[Union[QuoteStatus, str]] = None,
) -> Quote:
    """Create a new Quote entity with optional approval steps.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Account UUID string.
        title: Quote title string.
        total_amount: Total monetary amount for the quote.
        approver_ids: Optional list of approver UUID strings for multi-step workflow.
        status: Optional initial quote status.

    Returns:
        Created Quote model instance.
    """
    if not account_id or not account_id.strip():
        raise QuoteValidationError("Account ID must be provided and non-empty.")
    if not title or not title.strip():
        raise QuoteValidationError("Quote title must be provided and non-empty.")

    amount_dec = Decimal(str(total_amount))
    if amount_dec < Decimal("0.00"):
        raise QuoteValidationError("Total amount cannot be negative.")

    target_status = QuoteStatus.DRAFT
    if status:
        target_status = QuoteStatus(status) if isinstance(status, str) else status
    elif approver_ids and len(approver_ids) > 0:
        target_status = QuoteStatus.PENDING_APPROVAL

    now_utc = datetime.now(timezone.utc)
    quote = Quote(
        account_id=account_id.strip(),
        title=title.strip(),
        total_amount=amount_dec,
        status=target_status,
        created_at=now_utc,
        updated_at=now_utc,
    )

    if approver_ids:
        for idx, app_id in enumerate(approver_ids):
            if not app_id or not app_id.strip():
                raise QuoteValidationError("Approver ID cannot be empty.")
            approval_step = QuoteApproval(
                approver_id=app_id.strip(),
                step_number=idx + 1,
                status=QuoteApprovalStatus.PENDING,
                created_at=now_utc,
                updated_at=now_utc,
            )
            quote.approvals.append(approval_step)

    db.add(quote)
    db.commit()
    db.refresh(quote)
    logger.info(
        "Created quote %s for account %s in status %s",
        quote.id,
        account_id,
        quote.status.value,
    )
    return quote


def submit_quote_for_approval(
    db: Session,
    quote_id: str,
    approver_ids: List[str],
) -> Quote:
    """Submit a draft quote for multi-step approval workflow with concurrency locking.

    Args:
        db: Synchronous SQLAlchemy database session.
        quote_id: Quote UUID string.
        approver_ids: Non-empty list of approver UUID strings.

    Returns:
        Updated Quote model instance.
    """
    if not approver_ids or len(approver_ids) == 0:
        raise QuoteValidationError("At least one approver ID must be specified for approval submission.")

    stmt = select(Quote).where(Quote.id == quote_id).with_for_update()
    quote = db.scalars(stmt).first()
    if not quote:
        raise QuoteNotFoundError(f"Quote with ID '{quote_id}' not found.")

    if quote.status != QuoteStatus.DRAFT:
        raise QuoteConflictError(
            f"Cannot submit quote '{quote_id}' for approval: current status is '{quote.status.value}', expected 'DRAFT'."
        )

    now_utc = datetime.now(timezone.utc)
    quote.approvals.clear()

    for idx, app_id in enumerate(approver_ids):
        if not app_id or not app_id.strip():
            raise QuoteValidationError("Approver ID cannot be empty.")
        quote.approvals.append(
            QuoteApproval(
                approver_id=app_id.strip(),
                step_number=idx + 1,
                status=QuoteApprovalStatus.PENDING,
                created_at=now_utc,
                updated_at=now_utc,
            )
        )

    quote.status = QuoteStatus.PENDING_APPROVAL
    quote.updated_at = now_utc

    db.add(quote)
    db.commit()
    db.refresh(quote)
    logger.info("Submitted quote %s for approval with %d steps", quote_id, len(approver_ids))
    return quote


def _validate_approval_decision(
    decision: Union[QuoteApprovalStatus, str],
) -> QuoteApprovalStatus:
    """Validate and convert decision parameter to QuoteApprovalStatus enum."""
    if isinstance(decision, str):
        try:
            decision_enum = QuoteApprovalStatus(decision.upper())
        except ValueError:
            raise QuoteValidationError(
                f"Invalid approval decision '{decision}'. Expected APPROVED or REJECTED."
            )
    else:
        decision_enum = decision

    if decision_enum not in (QuoteApprovalStatus.APPROVED, QuoteApprovalStatus.REJECTED):
        raise QuoteValidationError("Approval decision must be APPROVED or REJECTED.")
    return decision_enum


def _verify_step_sequence(
    quote: Quote,
    step_number: int,
) -> QuoteApproval:
    """Verify target approval step exists and prior steps are all approved."""
    approvals_by_step = {step.step_number: step for step in quote.approvals}
    target_step = approvals_by_step.get(step_number)
    if not target_step:
        raise QuoteValidationError(
            f"Approval step {step_number} does not exist for quote '{quote.id}'."
        )

    for prev_step_num in range(1, step_number):
        prev_step = approvals_by_step.get(prev_step_num)
        if not prev_step or prev_step.status != QuoteApprovalStatus.APPROVED:
            raise QuoteConflictError(
                f"Out-of-order approval submission: step {prev_step_num} must be APPROVED before step {step_number} can be decided."
            )

    if target_step.status != QuoteApprovalStatus.PENDING:
        raise QuoteConflictError(
            f"Approval step {step_number} for quote '{quote.id}' has already been decided with status '{target_step.status.value}'."
        )

    return target_step


def process_quote_approval(
    db: Session,
    quote_id: str,
    step_number: int,
    decision: Union[QuoteApprovalStatus, str],
    approver_id: Optional[str] = None,
    comment: Optional[str] = None,
) -> Quote:
    """Process a manager approval or rejection decision for a specific workflow step.

    Employs row-level database locking (with_for_update) to prevent race conditions
    and validates sequential step execution to enforce multi-step approval integrity.

    Args:
        db: Synchronous SQLAlchemy database session.
        quote_id: Quote UUID string.
        step_number: 1-indexed step number to decide upon.
        decision: Approval decision (APPROVED or REJECTED).
        approver_id: Optional approver UUID string submitting the decision.
        comment: Optional manager feedback comment.

    Returns:
        Updated Quote model instance.
    """
    stmt = select(Quote).where(Quote.id == quote_id).with_for_update()
    quote = db.scalars(stmt).first()
    if not quote:
        raise QuoteNotFoundError(f"Quote with ID '{quote_id}' not found.")

    if quote.status != QuoteStatus.PENDING_APPROVAL:
        raise QuoteConflictError(
            f"Cannot process approval decision for quote '{quote_id}': current status is '{quote.status.value}', expected 'PENDING_APPROVAL'."
        )

    decision_enum = _validate_approval_decision(decision)
    target_step = _verify_step_sequence(quote, step_number)

    if approver_id and target_step.approver_id and target_step.approver_id != approver_id:
        raise QuoteConflictError(
            f"Approver '{approver_id}' is not authorized to approve step {step_number} (assigned to '{target_step.approver_id}')."
        )

    now_utc = datetime.now(timezone.utc)
    target_step.status = decision_enum
    target_step.decided_at = now_utc
    target_step.comment = comment
    target_step.updated_at = now_utc
    if approver_id:
        target_step.approver_id = approver_id

    if decision_enum == QuoteApprovalStatus.REJECTED:
        quote.status = QuoteStatus.REJECTED
        logger.info("Quote %s rejected at step %d", quote_id, step_number)
    else:
        all_approved = all(step.status == QuoteApprovalStatus.APPROVED for step in quote.approvals)
        if all_approved:
            quote.status = QuoteStatus.APPROVED
            logger.info("Quote %s fully approved across all %d steps", quote_id, len(quote.approvals))
        else:
            logger.info("Quote %s step %d approved; pending remaining steps", quote_id, step_number)

    quote.updated_at = now_utc

    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


def update_quote(
    db: Session,
    quote_id: str,
    title: Optional[str] = None,
    total_amount: Optional[Union[Decimal, float, int]] = None,
) -> Quote:
    """Update title or total amount of a draft quote.

    Args:
        db: Synchronous SQLAlchemy database session.
        quote_id: Quote UUID string.
        title: Optional new title.
        total_amount: Optional new total amount.

    Returns:
        Updated Quote model instance.
    """
    stmt = select(Quote).where(Quote.id == quote_id).with_for_update()
    quote = db.scalars(stmt).first()
    if not quote:
        raise QuoteNotFoundError(f"Quote with ID '{quote_id}' not found.")

    if quote.status != QuoteStatus.DRAFT:
        raise QuoteConflictError(
            f"Cannot modify quote '{quote_id}': current status is '{quote.status.value}', expected 'DRAFT'."
        )

    if title is not None:
        if not title.strip():
            raise QuoteValidationError("Quote title cannot be empty.")
        quote.title = title.strip()

    if total_amount is not None:
        amount_dec = Decimal(str(total_amount))
        if amount_dec < Decimal("0.00"):
            raise QuoteValidationError("Total amount cannot be negative.")
        quote.total_amount = amount_dec

    quote.updated_at = datetime.now(timezone.utc)
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


def delete_quote(db: Session, quote_id: str) -> None:
    """Delete a quote and its associated approval steps from the database.

    Args:
        db: Synchronous SQLAlchemy database session.
        quote_id: Quote UUID string.
    """
    stmt = select(Quote).where(Quote.id == quote_id)
    quote = db.scalars(stmt).first()
    if not quote:
        raise QuoteNotFoundError(f"Quote with ID '{quote_id}' not found.")

    db.delete(quote)
    db.commit()
    logger.info("Deleted quote %s", quote_id)
