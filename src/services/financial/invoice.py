"""Domain service for invoice tracking retrieval, creation, and payment status filtering."""

from datetime import date as date_type
from decimal import Decimal, InvalidOperation
import logging
from typing import List, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.invoice import Invoice, PaymentStatus

logger = logging.getLogger(__name__)


class InvoiceServiceError(Exception):
    """Base exception class for invoice domain service operations."""

    pass


class InvoiceNotFoundError(InvoiceServiceError):
    """Raised when a requested invoice record cannot be found."""

    pass


class InvoiceValidationError(InvoiceServiceError):
    """Raised when invoice inputs or parameters fail validation."""

    pass


class InvoiceConflictError(InvoiceServiceError):
    """Raised when invoice record creation conflicts with existing data."""

    pass


def parse_payment_status(status_input: Union[PaymentStatus, str]) -> PaymentStatus:
    """Parse and validate a payment status input into a PaymentStatus enum instance.

    Args:
        status_input: PaymentStatus enum or string value.

    Returns:
        PaymentStatus enum value.

    Raises:
        InvoiceValidationError: If status string is invalid.
    """
    if isinstance(status_input, PaymentStatus):
        return status_input

    if not isinstance(status_input, str) or not status_input.strip():
        raise InvoiceValidationError("Payment status string cannot be empty")

    cleaned_status = status_input.strip().upper()
    try:
        return PaymentStatus(cleaned_status)
    except ValueError:
        valid_statuses = [e.value for e in PaymentStatus]
        raise InvoiceValidationError(
            f"Invalid payment status '{status_input}'. Must be one of {valid_statuses}"
        )


def _parse_date_value(date_val: Union[date_type, str], field_name: str) -> date_type:
    """Parse a date input value into a datetime.date object.

    Args:
        date_val: Date object or ISO-formatted date string (YYYY-MM-DD).
        field_name: Field descriptor for exception error context.

    Returns:
        datetime.date instance.

    Raises:
        InvoiceValidationError: If date string cannot be parsed.
    """
    if isinstance(date_val, date_type):
        return date_val

    if isinstance(date_val, str) and date_val.strip():
        try:
            return date_type.fromisoformat(date_val.strip())
        except ValueError:
            raise InvoiceValidationError(
                f"Invalid date format for {field_name}: '{date_val}'. Expected YYYY-MM-DD"
            )

    raise InvoiceValidationError(f"{field_name} must be provided as a date object or ISO string")


def get_invoice_by_id(db: Session, invoice_id: str) -> Optional[Invoice]:
    """Retrieve an Invoice entity by ID.

    Args:
        db: Synchronous SQLAlchemy database session.
        invoice_id: UUID string of the target invoice.

    Returns:
        Invoice model instance if found, otherwise None.
    """
    if not invoice_id or not invoice_id.strip():
        return None
    stmt = select(Invoice).where(Invoice.id == invoice_id.strip())
    return db.scalars(stmt).first()


def get_invoice_by_id_or_raise(db: Session, invoice_id: str) -> Invoice:
    """Retrieve an Invoice entity by ID or raise InvoiceNotFoundError if not found.

    Args:
        db: Synchronous SQLAlchemy database session.
        invoice_id: UUID string of the target invoice.

    Returns:
        Invoice model instance.

    Raises:
        InvoiceNotFoundError: If no invoice exists with the given ID.
    """
    invoice = get_invoice_by_id(db, invoice_id)
    if not invoice:
        raise InvoiceNotFoundError(f"Invoice with ID '{invoice_id}' not found")
    return invoice


def get_invoice_by_number(db: Session, invoice_number: str) -> Optional[Invoice]:
    """Retrieve an Invoice entity by its unique invoice number.

    Args:
        db: Synchronous SQLAlchemy database session.
        invoice_number: Unique invoice number string.

    Returns:
        Invoice model instance if found, otherwise None.
    """
    if not invoice_number or not invoice_number.strip():
        return None
    stmt = select(Invoice).where(Invoice.invoice_number == invoice_number.strip())
    return db.scalars(stmt).first()


def get_invoices(
    db: Session,
    account_id: Optional[str] = None,
    payment_status: Optional[Union[PaymentStatus, str]] = None,
) -> List[Invoice]:
    """Retrieve invoices optionally filtered by account ID and payment status.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Optional account UUID string filter.
        payment_status: Optional payment status filter (PaymentStatus enum or string).

    Returns:
        List of Invoice model instances ordered by created_at descending.
    """
    stmt = select(Invoice)

    if account_id and account_id.strip():
        stmt = stmt.where(Invoice.account_id == account_id.strip())

    if payment_status is not None:
        status_enum = parse_payment_status(payment_status)
        stmt = stmt.where(Invoice.payment_status == status_enum)

    stmt = stmt.order_by(Invoice.created_at.desc())
    return list(db.scalars(stmt).all())


def create_invoice(
    db: Session,
    account_id: str,
    invoice_number: str,
    issue_date: Union[date_type, str],
    due_date: Union[date_type, str],
    amount: Union[Decimal, float, int, str],
    payment_status: Optional[Union[PaymentStatus, str]] = PaymentStatus.UNPAID,
) -> Invoice:
    """Create a new Invoice entity in the database.

    Args:
        db: Synchronous SQLAlchemy database session.
        account_id: Account UUID string.
        invoice_number: Unique invoice number string.
        issue_date: Issue date object or YYYY-MM-DD string.
        due_date: Due date object or YYYY-MM-DD string.
        amount: Invoice total monetary amount.
        payment_status: Initial payment status (default: UNPAID).

    Returns:
        Created Invoice model instance.

    Raises:
        InvoiceValidationError: If inputs are invalid or negative amount provided.
        InvoiceConflictError: If invoice_number already exists in database.
    """
    if not account_id or not account_id.strip():
        raise InvoiceValidationError("Account ID must be provided")

    if not invoice_number or not invoice_number.strip():
        raise InvoiceValidationError("Invoice number must be provided")

    clean_acc_id = account_id.strip()
    clean_inv_num = invoice_number.strip()

    if get_invoice_by_number(db, clean_inv_num) is not None:
        raise InvoiceConflictError(f"Invoice with number '{clean_inv_num}' already exists")

    parsed_issue_date = _parse_date_value(issue_date, "issue_date")
    parsed_due_date = _parse_date_value(due_date, "due_date")

    try:
        dec_amount = Decimal(str(amount))
    except (ValueError, TypeError, InvalidOperation):
        raise InvoiceValidationError(f"Invalid monetary amount '{amount}'")

    if dec_amount < Decimal("0.00"):
        raise InvoiceValidationError("Invoice amount cannot be negative")

    status_enum = parse_payment_status(payment_status or PaymentStatus.UNPAID)

    invoice = Invoice(
        account_id=clean_acc_id,
        invoice_number=clean_inv_num,
        issue_date=parsed_issue_date,
        due_date=parsed_due_date,
        amount=dec_amount,
        payment_status=status_enum,
    )

    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    logger.info("Created invoice %s for account %s", invoice.id, clean_acc_id)
    return invoice


def update_invoice_payment_status(
    db: Session,
    invoice_id: str,
    payment_status: Union[PaymentStatus, str],
) -> Invoice:
    """Update payment status of an existing Invoice record.

    Args:
        db: Synchronous SQLAlchemy database session.
        invoice_id: Target invoice UUID string.
        payment_status: New payment status (PaymentStatus enum or string).

    Returns:
        Updated Invoice model instance.

    Raises:
        InvoiceNotFoundError: If target invoice does not exist.
        InvoiceValidationError: If payment status input is invalid.
    """
    invoice = get_invoice_by_id_or_raise(db, invoice_id)
    status_enum = parse_payment_status(payment_status)

    invoice.payment_status = status_enum
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    logger.info("Updated invoice %s payment_status to %s", invoice.id, status_enum.value)
    return invoice


def update_invoice(
    db: Session,
    invoice_id: str,
    amount: Optional[Union[Decimal, float, int, str]] = None,
    payment_status: Optional[Union[PaymentStatus, str]] = None,
    issue_date: Optional[Union[date_type, str]] = None,
    due_date: Optional[Union[date_type, str]] = None,
) -> Invoice:
    """Update fields on an existing Invoice record.

    Args:
        db: Synchronous SQLAlchemy database session.
        invoice_id: Target invoice UUID string.
        amount: Optional new monetary amount.
        payment_status: Optional new payment status.
        issue_date: Optional new issue date.
        due_date: Optional new due date.

    Returns:
        Updated Invoice model instance.
    """
    invoice = get_invoice_by_id_or_raise(db, invoice_id)

    if amount is not None:
        try:
            dec_amount = Decimal(str(amount))
        except (ValueError, TypeError, InvalidOperation):
            raise InvoiceValidationError(f"Invalid monetary amount '{amount}'")
        if dec_amount < Decimal("0.00"):
            raise InvoiceValidationError("Invoice amount cannot be negative")
        invoice.amount = dec_amount

    if payment_status is not None:
        invoice.payment_status = parse_payment_status(payment_status)

    if issue_date is not None:
        invoice.issue_date = _parse_date_value(issue_date, "issue_date")

    if due_date is not None:
        invoice.due_date = _parse_date_value(due_date, "due_date")

    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    logger.info("Updated fields for invoice %s", invoice.id)
    return invoice


def delete_invoice(db: Session, invoice_id: str) -> bool:
    """Delete an Invoice record by ID.

    Args:
        db: Synchronous SQLAlchemy database session.
        invoice_id: Target invoice UUID string.

    Returns:
        True if successfully deleted.

    Raises:
        InvoiceNotFoundError: If target invoice does not exist.
    """
    invoice = get_invoice_by_id_or_raise(db, invoice_id)
    db.delete(invoice)
    db.commit()
    logger.info("Deleted invoice %s", invoice_id)
    return True
