"""FastAPI router for support tickets, replies, attachments, status updates, and CSAT submission."""

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.connectors.storage import AttachmentStorageAdapter, AttachmentValidationError
from src.models.audit_log import TicketEvent, TicketEventType
from src.models.base import get_db
from src.models.csat import CSATResponse
from src.models.session import Session as SessionModel
from src.models.ticket import TicketSeverity, TicketStatus
from src.services.escalation.ticket_service import (
    TicketNotFoundError,
    TicketPermissionDeniedError,
    TicketValidationError,
    add_ticket_event,
    create_ticket,
    delete_ticket,
    get_ticket_by_id,
    get_ticket_events,
    list_tickets,
    update_ticket_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


# --- Schemas ---


class TicketCreateSchema(BaseModel):
    """Pydantic schema for creating a support ticket via JSON."""

    title: str = Field(..., min_length=1, max_length=255, description="Ticket title")
    description: str = Field(..., min_length=1, description="Detailed problem description")
    severity: TicketSeverity = Field(default=TicketSeverity.P3, description="Ticket impact severity (P1, P2, P3)")
    organization_id: Optional[str] = Field(None, description="Optional organization ID override")


class TicketResponseSchema(BaseModel):
    """Pydantic schema for ticket summary responses."""

    id: str
    title: str
    description: str
    severity: TicketSeverity
    status: TicketStatus
    escalation_tier: int
    organization_id: str
    creator_user_id: str
    assigned_agent_id: Optional[str] = None
    response_due_at: datetime
    resolution_due_at: datetime
    is_sla_breached: bool
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedTicketsResponseSchema(BaseModel):
    """Pydantic schema for paginated ticket search results."""

    items: List[TicketResponseSchema]
    total: int
    page: int
    limit: int
    pages: int

    model_config = ConfigDict(from_attributes=True)


class TicketEventResponseSchema(BaseModel):
    """Pydantic schema for ticket audit event entries."""

    id: str
    ticket_id: str
    author_id: str
    event_type: TicketEventType
    content: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_filename: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketDetailResponseSchema(TicketResponseSchema):
    """Pydantic schema for detailed ticket view with event history."""

    events: List[TicketEventResponseSchema] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ReplyCreateSchema(BaseModel):
    """Pydantic schema for adding a comment/reply to a ticket."""

    content: Optional[str] = Field(None, description="Text reply or comment content")


class StatusUpdateSchema(BaseModel):
    """Pydantic schema for updating ticket status, severity, or agent assignment."""

    status: Optional[TicketStatus] = Field(None, description="Target ticket status state")
    severity: Optional[TicketSeverity] = Field(None, description="Reclassified severity impact")
    assigned_agent_id: Optional[str] = Field(None, description="Assigned agent user ID")
    comment: Optional[str] = Field(None, description="Explanation comment for status or severity update")


class CSATCreateSchema(BaseModel):
    """Pydantic schema for submitting CSAT survey feedback."""

    rating: int = Field(..., ge=1, le=5, description="CSAT rating score from 1 to 5")
    feedback_text: Optional[str] = Field(None, description="Optional qualitative feedback text")


class CSATResponseSchema(BaseModel):
    """Pydantic schema for CSAT survey response."""

    id: str
    ticket_id: str
    rating: int
    feedback_text: Optional[str] = None
    user_id: str
    submitted_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Helpers ---


def check_tenant_boundary(target_org_id: Optional[str], session: SessionModel) -> str:
    """Verify tenant isolation boundary between active session and requested organization.

    Args:
        target_org_id: Requested target organization ID.
        session: Active user session.

    Returns:
        Validated target organization ID string.

    Raises:
        HTTPException: 403 Forbidden if tenant isolation boundary is breached.
    """
    if not target_org_id:
        return session.tenant_id

    org_clean = target_org_id.strip()
    session_org = (session.tenant_id or "").strip()

    if session_org.lower() in {"*", "admin", "superadmin", "all", "system"}:
        return org_clean

    if org_clean != session_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TENANT_MISMATCH",
                "message": f"Access denied: Session tenant '{session_org}' cannot access organization '{org_clean}'.",
                "details": {
                    "session_tenant_id": session_org,
                    "target_tenant_id": org_clean,
                },
            },
        )
    return org_clean


# --- Endpoints ---


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TicketResponseSchema,
    summary="Submit support ticket with attachment",
    description="Submit a new support ticket with title, description, severity, organization_id, and optional attachment file.",
)
async def create_ticket_endpoint(
    request: Request,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> TicketResponseSchema:
    """Create a new support ticket handling both multipart form data and JSON payloads."""
    content_type = request.headers.get("content-type", "").lower()
    title: Optional[str] = None
    description: Optional[str] = None
    severity: str = "P3"
    organization_id: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_content: Optional[bytes] = None

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        title = str(form.get("title", "") or "")
        description = str(form.get("description", "") or "")
        severity = str(form.get("severity", "P3") or "P3")
        org_val = form.get("organization_id")
        organization_id = str(org_val) if org_val else None
        file_val = form.get("file")
        if file_val and hasattr(file_val, "filename") and file_val.filename:
            attachment_filename = file_val.filename
            attachment_content = await file_val.read()
    else:
        try:
            json_data = await request.json()
        except Exception:
            json_data = {}
        title = json_data.get("title")
        description = json_data.get("description")
        severity = json_data.get("severity", "P3")
        organization_id = json_data.get("organization_id")

    if not organization_id:
        organization_id = session.tenant_id

    target_org_id = check_tenant_boundary(organization_id, session)

    try:
        ticket = create_ticket(
            db=db,
            title=title or "",
            description=description or "",
            severity=severity,
            organization_id=target_org_id,
            creator_user_id=session.user_id,
            attachment_filename=attachment_filename,
            attachment_content=attachment_content,
        )
        return TicketResponseSchema.model_validate(ticket)
    except (TicketValidationError, AttachmentValidationError) as err:
        logger.warning("Ticket creation validation failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        logger.warning("Ticket creation boundary error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )


@router.get(
    "",
    response_model=PaginatedTicketsResponseSchema,
    summary="Query tickets for authorized tenant",
    description="Query a paginated list of support tickets filtered by status, severity, agent, or SLA breach status.",
)
def list_tickets_endpoint(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (NEW, IN_PROGRESS, ESCALATED, RESOLVED, CLOSED)"),
    severity_filter: Optional[str] = Query(None, alias="severity", description="Filter by severity impact (P1, P2, P3)"),
    organization_id: Optional[str] = Query(None, description="Filter by organization ID"),
    assigned_agent_id: Optional[str] = Query(None, description="Filter by assigned agent ID"),
    creator_user_id: Optional[str] = Query(None, description="Filter by creator user ID"),
    is_sla_breached: Optional[bool] = Query(None, description="Filter by SLA breach status"),
    page: int = Query(1, ge=1, description="Page number (1-based index)"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> PaginatedTicketsResponseSchema:
    """Retrieve paginated list of support tickets filtered by query criteria."""
    target_org_id = check_tenant_boundary(organization_id, session) if organization_id else session.tenant_id

    try:
        tickets, total_count = list_tickets(
            db=db,
            organization_id=target_org_id,
            status=status_filter,
            severity=severity_filter,
            assigned_agent_id=assigned_agent_id,
            creator_user_id=creator_user_id,
            is_sla_breached=is_sla_breached,
            page=page,
            limit=limit,
        )
        pages = (total_count + limit - 1) // limit if total_count > 0 else 0
        return PaginatedTicketsResponseSchema(
            items=[TicketResponseSchema.model_validate(t) for t in tickets],
            total=total_count,
            page=page,
            limit=limit,
            pages=pages,
        )
    except TicketValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(err), "details": {}},
        )


@router.get(
    "/{id}",
    response_model=TicketDetailResponseSchema,
    summary="Get support ticket details and event history",
    description="Retrieve support ticket details by ID along with its full chronological audit event log.",
)
def get_ticket_detail_endpoint(
    id: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> TicketDetailResponseSchema:
    """Retrieve details and chronological event history for a specific support ticket."""
    try:
        ticket = get_ticket_by_id(db, ticket_id=id, user_organization_id=session.tenant_id)
        events = get_ticket_events(db, ticket_id=id, user_organization_id=session.tenant_id)
        ticket_dict = TicketResponseSchema.model_validate(ticket).model_dump()
        ticket_dict["events"] = [TicketEventResponseSchema.model_validate(e) for e in events]
        return TicketDetailResponseSchema(**ticket_dict)
    except TicketNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )


@router.post(
    "/{id}/replies",
    status_code=status.HTTP_201_CREATED,
    response_model=TicketEventResponseSchema,
    summary="Add reply or attachment to ticket",
    description="Add a comment, reply, or file attachment to a support ticket audit event log.",
)
async def add_ticket_reply_endpoint(
    id: str,
    request: Request,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> TicketEventResponseSchema:
    """Add a reply or attachment to a support ticket handling both multipart and JSON formats."""
    content_type = request.headers.get("content-type", "").lower()
    content: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_content: Optional[bytes] = None

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw_content = form.get("content")
        content = str(raw_content) if raw_content else None
        file_val = form.get("file")
        if file_val and hasattr(file_val, "filename") and file_val.filename:
            attachment_filename = file_val.filename
            attachment_content = await file_val.read()
    else:
        try:
            json_data = await request.json()
        except Exception:
            json_data = {}
        content = json_data.get("content")

    try:
        event = add_ticket_event(
            db=db,
            ticket_id=id,
            author_id=session.user_id,
            content=content,
            attachment_filename=attachment_filename,
            attachment_content=attachment_content,
            user_organization_id=session.tenant_id,
        )
        return TicketEventResponseSchema.model_validate(event)
    except TicketNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )
    except (TicketValidationError, AttachmentValidationError) as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(err), "details": {}},
        )


@router.patch(
    "/{id}/status",
    response_model=TicketResponseSchema,
    summary="Update ticket status or severity",
    description="Update support ticket status state, severity classification (with SLA recalibration), or assigned agent.",
)
def update_ticket_status_endpoint(
    id: str,
    payload: StatusUpdateSchema = Body(...),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> TicketResponseSchema:
    """Update support ticket status state, severity, or agent assignment."""
    try:
        ticket = update_ticket_status(
            db=db,
            ticket_id=id,
            author_id=session.user_id,
            new_status=payload.status,
            new_severity=payload.severity,
            assigned_agent_id=payload.assigned_agent_id,
            comment=payload.comment,
            user_organization_id=session.tenant_id,
        )
        return TicketResponseSchema.model_validate(ticket)
    except TicketNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )
    except TicketValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(err), "details": {}},
        )


@router.post(
    "/{id}/csat",
    status_code=status.HTTP_201_CREATED,
    response_model=CSATResponseSchema,
    summary="Submit post-resolution CSAT survey",
    description="Submit a CSAT rating score (1-5) and feedback for a resolved or closed support ticket.",
)
def submit_csat_endpoint(
    id: str,
    payload: CSATCreateSchema = Body(...),
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> CSATResponseSchema:
    """Submit post-resolution CSAT rating and optional feedback."""
    try:
        ticket = get_ticket_by_id(db, ticket_id=id, user_organization_id=session.tenant_id)

        if ticket.status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            raise TicketValidationError("CSAT feedback can only be submitted for resolved or closed tickets.")

        existing_csat = db.query(CSATResponse).filter(CSATResponse.ticket_id == ticket.id).first()
        if existing_csat:
            raise TicketValidationError("CSAT survey response has already been submitted for this ticket.")

        if payload.rating < 1 or payload.rating > 5:
            raise TicketValidationError("CSAT rating score must be between 1 and 5.")

        now = datetime.now(timezone.utc)
        csat = CSATResponse(
            ticket_id=ticket.id,
            rating=payload.rating,
            feedback_text=payload.feedback_text.strip() if payload.feedback_text else None,
            user_id=session.user_id,
            submitted_at=now,
        )
        db.add(csat)

        csat_event = TicketEvent(
            ticket_id=ticket.id,
            author_id=session.user_id,
            event_type=TicketEventType.CSAT_SUBMITTED,
            content=f"CSAT submitted with rating {payload.rating}/5.",
            created_at=now,
        )
        db.add(csat_event)
        db.commit()
        db.refresh(csat)

        logger.info("Submitted CSAT for ticket %s (rating=%d, user=%s)", ticket.id, payload.rating, session.user_id)
        return CSATResponseSchema.model_validate(csat)
    except TicketNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )
    except TicketValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": str(err), "details": {}},
        )


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete support ticket record",
    description="Permanently delete a support ticket record and cascade delete associated events and CSAT records.",
)
def delete_ticket_endpoint(
    id: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> Response:
    """Delete support ticket record permanently."""
    try:
        delete_ticket(db, ticket_id=id, user_organization_id=session.tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except TicketNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )


@router.get(
    "/{ticket_id}/attachments/{filename}",
    summary="Download ticket attachment file",
    description="Retrieve and download stored file attachment for a support ticket.",
)
def download_attachment_endpoint(
    ticket_id: str,
    filename: str,
    db: DBSession = Depends(get_db),
    session: SessionModel = Depends(get_current_session),
) -> Response:
    """Download stored attachment file for a support ticket."""
    try:
        ticket = get_ticket_by_id(db, ticket_id=ticket_id, user_organization_id=session.tenant_id)
        storage_adapter = AttachmentStorageAdapter()
        content = storage_adapter.get_attachment(os.path.join(ticket.id, filename))
        return Response(content=content, media_type="application/octet-stream")
    except TicketNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(err), "details": {}},
        )
    except TicketPermissionDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(err), "details": {}},
        )
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Attachment file not found: {err}", "details": {}},
        )
