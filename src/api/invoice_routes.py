"""FastAPI router for invoice list tracking, status filtering, and invoice detail management."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.models.base import get_db
from src.models.invoice import Invoice, PaymentStatus
from src.services.financial.invoice import (
    InvoiceConflictError,
    InvoiceNotFoundError,
    InvoiceValidationError,
    create_invoice,
    delete_invoice,
    get_invoice_by_id_or_raise,
    get_invoices,
    update_invoice,
    update_invoice_payment_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/financial/invoices", tags=["invoices"])


# --- Schemas ---


class InvoiceResponseSchema(BaseModel):
    """Pydantic schema for an invoice response."""

    id: str = Field(..., description="Unique invoice UUID")
    account_id: str = Field(..., description="Account UUID")
    invoice_number: str = Field(..., description="Unique invoice identifier string")
    issue_date: str = Field(..., description="Invoice issue date string (YYYY-MM-DD)")
    due_date: str = Field(..., description="Invoice payment due date string (YYYY-MM-DD)")
    amount: float = Field(..., description="Monetary total amount of the invoice")
    payment_status: str = Field(
        ..., description="Payment status (UNPAID, PAID, OVERDUE, PARTIALLY_PAID)"
    )
    created_at: str = Field(..., description="ISO 8601 timestamp when invoice record was created")

    model_config = ConfigDict(from_attributes=True)


class InvoiceCreateSchema(BaseModel):
    """Pydantic schema for creating a new invoice record."""

    account_id: str = Field(..., description="Account UUID string")
    invoice_number: str = Field(..., description="Unique invoice identifier string")
    issue_date: str = Field(..., description="Invoice issue date string (YYYY-MM-DD)")
    due_date: str = Field(..., description="Invoice payment due date string (YYYY-MM-DD)")
    amount: float = Field(..., ge=0.0, description="Invoice total monetary amount")
    payment_status: Optional[str] = Field(
        "UNPAID", description="Initial payment status (default: UNPAID)"
    )


class InvoiceUpdateStatusSchema(BaseModel):
    """Pydantic schema for updating an invoice payment status."""

    payment_status: str = Field(
        ..., description="New payment status (UNPAID, PAID, OVERDUE, PARTIALLY_PAID)"
    )


class InvoiceUpdateSchema(BaseModel):
    """Pydantic schema for updating invoice fields."""

    amount: Optional[float] = Field(None, ge=0.0, description="Optional new monetary amount")
    payment_status: Optional[str] = Field(None, description="Optional new payment status")
    issue_date: Optional[str] = Field(None, description="Optional new issue date string (YYYY-MM-DD)")
    due_date: Optional[str] = Field(None, description="Optional new due date string (YYYY-MM-DD)")


def serialize_invoice(invoice: Invoice) -> Dict[str, Any]:
    """Helper function to convert an Invoice ORM model into a dictionary for InvoiceResponseSchema."""
    status_val = (
        invoice.payment_status.value
        if hasattr(invoice.payment_status, "value")
        else str(invoice.payment_status)
    )
    issue_str = (
        invoice.issue_date.isoformat()
        if hasattr(invoice.issue_date, "isoformat")
        else str(invoice.issue_date)
    )
    due_str = (
        invoice.due_date.isoformat()
        if hasattr(invoice.due_date, "isoformat")
        else str(invoice.due_date)
    )
    created_str = (
        invoice.created_at.isoformat()
        if hasattr(invoice.created_at, "isoformat")
        else str(invoice.created_at)
    )

    return {
        "id": invoice.id,
        "account_id": invoice.account_id,
        "invoice_number": invoice.invoice_number,
        "issue_date": issue_str,
        "due_date": due_str,
        "amount": float(invoice.amount),
        "payment_status": status_val,
        "created_at": created_str,
    }


# --- Endpoint Routes ---


@router.get(
    "",
    response_model=List[InvoiceResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="List invoices",
    description="Retrieve invoice records optionally filtered by account ID and payment status.",
)
def list_invoices_endpoint(
    account_id: Optional[str] = Query(None, description="Optional account UUID filter"),
    payment_status: Optional[str] = Query(
        None, description="Optional payment status filter (UNPAID, PAID, OVERDUE, PARTIALLY_PAID)"
    ),
    db: DBSession = Depends(get_db),
) -> List[InvoiceResponseSchema]:
    """API endpoint to retrieve invoice records with payment status filtering."""
    try:
        invoices = get_invoices(db, account_id=account_id, payment_status=payment_status)
        return [InvoiceResponseSchema(**serialize_invoice(inv)) for inv in invoices]
    except InvoiceValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error listing invoices: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_LIST_ERROR",
                "message": f"Failed to list invoices: {str(err)}",
                "details": {},
            },
        )


@router.post(
    "",
    response_model=InvoiceResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new invoice",
    description="Create a new invoice record in the system.",
)
def create_invoice_endpoint(
    payload: InvoiceCreateSchema,
    db: DBSession = Depends(get_db),
) -> InvoiceResponseSchema:
    """API endpoint to create a new invoice record."""
    try:
        invoice = create_invoice(
            db=db,
            account_id=payload.account_id,
            invoice_number=payload.invoice_number,
            issue_date=payload.issue_date,
            due_date=payload.due_date,
            amount=payload.amount,
            payment_status=payload.payment_status,
        )
        return InvoiceResponseSchema(**serialize_invoice(invoice))
    except InvoiceValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except InvoiceConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVOICE_CONFLICT", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error creating invoice: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_CREATE_ERROR",
                "message": f"Failed to create invoice: {str(err)}",
                "details": {},
            },
        )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get invoice details",
    description="Retrieve details for a single invoice by its unique ID.",
)
def get_invoice_endpoint(
    invoice_id: str,
    db: DBSession = Depends(get_db),
) -> InvoiceResponseSchema:
    """API endpoint to retrieve a single invoice by ID."""
    try:
        invoice = get_invoice_by_id_or_raise(db, invoice_id)
        return InvoiceResponseSchema(**serialize_invoice(invoice))
    except InvoiceNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVOICE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error retrieving invoice %s: %s", invoice_id, err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_GET_ERROR",
                "message": f"Failed to retrieve invoice: {str(err)}",
                "details": {},
            },
        )


@router.patch(
    "/{invoice_id}/status",
    response_model=InvoiceResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Update invoice payment status",
    description="Update the payment status of an existing invoice.",
)
def update_invoice_status_endpoint(
    invoice_id: str,
    payload: InvoiceUpdateStatusSchema,
    db: DBSession = Depends(get_db),
) -> InvoiceResponseSchema:
    """API endpoint to update payment status of an invoice."""
    try:
        invoice = update_invoice_payment_status(
            db=db,
            invoice_id=invoice_id,
            payment_status=payload.payment_status,
        )
        return InvoiceResponseSchema(**serialize_invoice(invoice))
    except InvoiceNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVOICE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except InvoiceValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error(
            "Error updating payment status for invoice %s: %s", invoice_id, err, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_STATUS_UPDATE_ERROR",
                "message": f"Failed to update invoice payment status: {str(err)}",
                "details": {},
            },
        )


@router.patch(
    "/{invoice_id}",
    response_model=InvoiceResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Update invoice details",
    description="Update editable fields for an existing invoice.",
)
def update_invoice_endpoint(
    invoice_id: str,
    payload: InvoiceUpdateSchema,
    db: DBSession = Depends(get_db),
) -> InvoiceResponseSchema:
    """API endpoint to update details of an invoice."""
    try:
        invoice = update_invoice(
            db=db,
            invoice_id=invoice_id,
            amount=payload.amount,
            payment_status=payload.payment_status,
            issue_date=payload.issue_date,
            due_date=payload.due_date,
        )
        return InvoiceResponseSchema(**serialize_invoice(invoice))
    except InvoiceNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVOICE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except InvoiceValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error updating invoice %s: %s", invoice_id, err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_UPDATE_ERROR",
                "message": f"Failed to update invoice: {str(err)}",
                "details": {},
            },
        )


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete invoice",
    description="Delete an invoice record by ID.",
)
def delete_invoice_endpoint(
    invoice_id: str,
    db: DBSession = Depends(get_db),
) -> Response:
    """API endpoint to delete an invoice record."""
    try:
        delete_invoice(db, invoice_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except InvoiceNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVOICE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error deleting invoice %s: %s", invoice_id, err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_DELETE_ERROR",
                "message": f"Failed to delete invoice: {str(err)}",
                "details": {},
            },
        )
