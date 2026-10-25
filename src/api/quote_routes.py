"""FastAPI router for quote status tracking, creation, submission, and multi-step approval workflows."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.models.base import get_db
from src.models.quote import Quote
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
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/financial/quotes", tags=["quotes"])


# --- Schemas ---


class QuoteApprovalResponseSchema(BaseModel):
    """Pydantic schema for a quote approval step response."""

    id: str = Field(..., description="Unique approval step UUID")
    quote_id: str = Field(..., description="Target quote UUID")
    approver_id: Optional[str] = Field(None, description="Assigned or deciding approver user ID")
    step_number: int = Field(..., ge=1, description="1-indexed sequence step number")
    status: str = Field(..., description="Approval step status (PENDING, APPROVED, REJECTED)")
    comment: Optional[str] = Field(None, description="Approver feedback comment")
    decided_at: Optional[str] = Field(None, description="ISO 8601 timestamp when step was decided")
    created_at: str = Field(..., description="ISO 8601 timestamp when step was created")
    updated_at: str = Field(..., description="ISO 8601 timestamp when step was updated")

    model_config = ConfigDict(from_attributes=True)


class QuoteResponseSchema(BaseModel):
    """Pydantic schema for a quote response with nested approval workflow steps."""

    id: str = Field(..., description="Unique quote UUID")
    account_id: str = Field(..., description="Account UUID")
    title: str = Field(..., description="Quote title")
    status: str = Field(..., description="Quote status (DRAFT, PENDING_APPROVAL, APPROVED, REJECTED)")
    total_amount: float = Field(..., description="Total monetary amount of quote")
    created_at: str = Field(..., description="ISO 8601 timestamp when quote was created")
    updated_at: str = Field(..., description="ISO 8601 timestamp when quote was updated")
    approval_steps: List[QuoteApprovalResponseSchema] = Field(
        default_factory=list,
        description="Nested list of approval workflow steps",
    )
    approvals: List[QuoteApprovalResponseSchema] = Field(
        default_factory=list,
        description="Nested list of approval workflow steps (alias for approval_steps)",
    )

    model_config = ConfigDict(from_attributes=True)


class QuoteCreateSchema(BaseModel):
    """Pydantic schema for creating a new quote."""

    account_id: str = Field(..., description="Account UUID string")
    title: str = Field(..., description="Quote title")
    total_amount: float = Field(..., ge=0.0, description="Total monetary amount for the quote")
    approver_ids: Optional[List[str]] = Field(
        None, description="Optional ordered list of approver user IDs for multi-step workflow"
    )


class QuoteSubmitSchema(BaseModel):
    """Pydantic schema for submitting a draft quote for approval."""

    approver_ids: List[str] = Field(
        ..., min_length=1, description="Ordered list of approver user IDs for approval workflow"
    )


class QuoteApprovalSubmissionSchema(BaseModel):
    """Pydantic schema for submitting a manager approval or rejection decision."""

    step_number: int = Field(..., ge=1, description="1-indexed step number to decide upon")
    decision: str = Field(..., description="Approval decision ('APPROVED' or 'REJECTED')")
    comment: Optional[str] = Field(None, description="Optional feedback comment")
    approver_id: Optional[str] = Field(None, description="Optional approver user ID submitting the decision")


def serialize_quote(quote: Quote) -> Dict[str, Any]:
    """Helper function to convert a Quote ORM model into a dictionary suitable for QuoteResponseSchema."""
    approvals_data = []
    if quote.approvals:
        for app in sorted(quote.approvals, key=lambda a: a.step_number):
            decided_iso = app.decided_at.isoformat() if app.decided_at else None
            status_val = app.status.value if hasattr(app.status, "value") else str(app.status)
            approvals_data.append(
                {
                    "id": app.id,
                    "quote_id": app.quote_id,
                    "approver_id": app.approver_id,
                    "step_number": app.step_number,
                    "status": status_val,
                    "comment": app.comment,
                    "decided_at": decided_iso,
                    "created_at": app.created_at.isoformat() if app.created_at else "",
                    "updated_at": app.updated_at.isoformat() if app.updated_at else "",
                }
            )

    quote_status = quote.status.value if hasattr(quote.status, "value") else str(quote.status)
    return {
        "id": quote.id,
        "account_id": quote.account_id,
        "title": quote.title,
        "status": quote_status,
        "total_amount": float(quote.total_amount),
        "created_at": quote.created_at.isoformat() if quote.created_at else "",
        "updated_at": quote.updated_at.isoformat() if quote.updated_at else "",
        "approval_steps": approvals_data,
        "approvals": approvals_data,
    }


# --- Endpoint Routes ---


@router.get(
    "",
    response_model=List[QuoteResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="List quotes",
    description="Retrieve quotes optionally filtered by account ID and lifecycle status.",
)
def list_quotes_endpoint(
    account_id: Optional[str] = Query(None, description="Optional account UUID filter"),
    status: Optional[str] = Query(None, description="Optional quote status filter (DRAFT, PENDING_APPROVAL, APPROVED, REJECTED)"),
    db: DBSession = Depends(get_db),
) -> List[QuoteResponseSchema]:
    """API endpoint to retrieve quote records with nested approval workflow details."""
    try:
        quotes = get_quotes(db, account_id=account_id, status=status)
        return [QuoteResponseSchema(**serialize_quote(q)) for q in quotes]
    except QuoteValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error listing quotes: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUOTE_LIST_ERROR", "message": f"Failed to list quotes: {str(err)}", "details": {}},
        )


@router.post(
    "",
    response_model=QuoteResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new quote",
    description="Create a new commercial quote record with optional initial approver sequence.",
)
def create_quote_endpoint(
    payload: QuoteCreateSchema,
    db: DBSession = Depends(get_db),
) -> QuoteResponseSchema:
    """API endpoint to create a quote record."""
    try:
        quote = create_quote(
            db=db,
            account_id=payload.account_id,
            title=payload.title,
            total_amount=payload.total_amount,
            approver_ids=payload.approver_ids,
        )
        return QuoteResponseSchema(**serialize_quote(quote))
    except QuoteValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error creating quote: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUOTE_CREATE_ERROR", "message": f"Failed to create quote: {str(err)}", "details": {}},
        )


@router.get(
    "/{quote_id}",
    response_model=QuoteResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get quote details by ID",
    description="Retrieve a single quote record with nested approval steps by quote ID.",
)
def get_quote_endpoint(
    quote_id: str,
    db: DBSession = Depends(get_db),
) -> QuoteResponseSchema:
    """API endpoint to retrieve a quote record by ID."""
    quote = get_quote_by_id(db, quote_id)
    if not quote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUOTE_NOT_FOUND", "message": f"Quote with ID '{quote_id}' not found.", "details": {}},
        )
    return QuoteResponseSchema(**serialize_quote(quote))


@router.post(
    "/{quote_id}/submit",
    response_model=QuoteResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Submit quote for approval",
    description="Submit a draft quote for multi-step management approval sequence.",
)
def submit_quote_endpoint(
    quote_id: str,
    payload: QuoteSubmitSchema,
    db: DBSession = Depends(get_db),
) -> QuoteResponseSchema:
    """API endpoint to submit a draft quote for multi-step approval."""
    try:
        quote = submit_quote_for_approval(
            db=db,
            quote_id=quote_id,
            approver_ids=payload.approver_ids,
        )
        return QuoteResponseSchema(**serialize_quote(quote))
    except QuoteNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUOTE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except QuoteConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "QUOTE_SUBMIT_CONFLICT", "message": str(err), "details": {}},
        )
    except QuoteValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error submitting quote %s for approval: %s", quote_id, err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUOTE_SUBMIT_ERROR", "message": f"Failed to submit quote for approval: {str(err)}", "details": {}},
        )


@router.post(
    "/{quote_id}/approve",
    response_model=QuoteResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Submit quote approval decision",
    description="Submit a manager approval or rejection decision for a specific workflow step in a multi-step quote approval process.",
)
def approve_quote_endpoint(
    quote_id: str,
    payload: QuoteApprovalSubmissionSchema,
    db: DBSession = Depends(get_db),
) -> QuoteResponseSchema:
    """API endpoint to submit an approval decision for a quote step."""
    try:
        updated_quote = process_quote_approval(
            db=db,
            quote_id=quote_id,
            step_number=payload.step_number,
            decision=payload.decision,
            approver_id=payload.approver_id,
            comment=payload.comment,
        )
        return QuoteResponseSchema(**serialize_quote(updated_quote))
    except QuoteNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUOTE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except QuoteConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "QUOTE_APPROVAL_CONFLICT", "message": str(err), "details": {}},
        )
    except QuoteValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error processing quote approval for quote %s: %s", quote_id, err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUOTE_APPROVAL_ERROR", "message": f"Failed to process quote approval: {str(err)}", "details": {}},
        )


@router.delete(
    "/{quote_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete quote",
    description="Delete a quote record and associated approval steps.",
)
def delete_quote_endpoint(
    quote_id: str,
    db: DBSession = Depends(get_db),
) -> Response:
    """API endpoint to delete a quote record."""
    try:
        delete_quote(db, quote_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except QuoteNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUOTE_NOT_FOUND", "message": str(err), "details": {}},
        )
    except Exception as err:
        logger.error("Error deleting quote %s: %s", quote_id, err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUOTE_DELETE_ERROR", "message": f"Failed to delete quote: {str(err)}", "details": {}},
        )
