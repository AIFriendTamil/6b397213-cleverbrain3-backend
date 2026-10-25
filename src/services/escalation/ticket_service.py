"""Support ticket management service handling lifecycle, event audit logging, and organization boundary access."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Union

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.audit_log import TicketEvent, TicketEventType
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus
from src.services.escalation.sla_engine import (
    calculate_sla_due_dates,
    recalibrate_ticket_sla,
)
from src.services.escalation.storage_port import AttachmentStoragePort

logger = logging.getLogger(__name__)


class TicketServiceError(Exception):
    """Base exception class for ticket management service errors."""

    pass


class TicketNotFoundError(TicketServiceError):
    """Raised when a requested support ticket cannot be found."""

    pass


class TicketPermissionDeniedError(TicketServiceError):
    """Raised when organization access control boundaries are breached."""

    pass


class TicketValidationError(TicketServiceError):
    """Raised when ticket payload or input validation fails."""

    pass


def check_organization_access(
    ticket: Ticket,
    user_organization_id: Optional[str],
) -> bool:
    """Verify that user's organization boundary permits access to the ticket.

    Args:
        ticket: Support ticket instance.
        user_organization_id: User's organization ID.

    Returns:
        True if authorized.

    Raises:
        TicketPermissionDeniedError: If organization boundary check fails.
    """
    if not user_organization_id:
        return True

    org_id_clean = user_organization_id.strip()
    allowed_wildcards = {"*", "admin", "superadmin", "all", "system"}

    if (
        org_id_clean.lower() in allowed_wildcards
        or org_id_clean == ticket.organization_id
    ):
        return True

    logger.warning(
        "Organization boundary breach attempt: user_org=%s, ticket_org=%s, ticket_id=%s",
        org_id_clean,
        ticket.organization_id,
        ticket.id,
    )
    raise TicketPermissionDeniedError(
        f"Access denied: Organization '{org_id_clean}' cannot access ticket in organization '{ticket.organization_id}'."
    )


def create_ticket(
    db: Session,
    title: str,
    description: str,
    severity: Union[TicketSeverity, str],
    organization_id: str,
    creator_user_id: str,
    attachment_filename: Optional[str] = None,
    attachment_content: Optional[bytes] = None,
    storage_port: Optional[AttachmentStoragePort] = None,
) -> Ticket:
    """Create a new support ticket and log initial creation audit event.

    Args:
        db: Database session.
        title: Support ticket title.
        description: Support ticket detailed description.
        severity: Priority classification (P1, P2, or P3).
        organization_id: Organization owning the ticket.
        creator_user_id: User ID of ticket creator.
        attachment_filename: Optional filename for initial attachment.
        attachment_content: Optional raw bytes for initial attachment.
        storage_port: Optional file attachment storage adapter port.

    Returns:
        Created Ticket ORM instance.

    Raises:
        TicketValidationError: If title, description, or parameters are invalid.
    """
    if not title or not title.strip():
        raise TicketValidationError("Ticket title cannot be empty.")
    if not description or not description.strip():
        raise TicketValidationError("Ticket description cannot be empty.")
    if not organization_id or not organization_id.strip():
        raise TicketValidationError("Organization ID cannot be empty.")
    if not creator_user_id or not creator_user_id.strip():
        raise TicketValidationError("Creator user ID cannot be empty.")

    if isinstance(severity, str):
        try:
            severity = TicketSeverity(severity.upper())
        except ValueError:
            raise TicketValidationError(f"Invalid ticket severity classification: '{severity}'.")

    now = datetime.now(timezone.utc)
    resp_due, res_due = calculate_sla_due_dates(severity, created_at=now)

    attachment_url = None
    stored_filename = None

    if attachment_filename and attachment_content:
        if storage_port is None:
            from src.connectors.storage import AttachmentStorageAdapter

            storage_port = AttachmentStorageAdapter()

        saved_meta = storage_port.save_attachment(
            filename=attachment_filename,
            content=attachment_content,
        )
        attachment_url = saved_meta.get("attachment_url")
        stored_filename = saved_meta.get("stored_filename", attachment_filename)

    ticket = Ticket(
        title=title.strip(),
        description=description.strip(),
        severity=severity,
        status=TicketStatus.NEW,
        escalation_tier=EscalationTier.TIER_1,
        organization_id=organization_id.strip(),
        creator_user_id=creator_user_id.strip(),
        response_due_at=resp_due,
        resolution_due_at=res_due,
        is_sla_breached=False,
        created_at=now,
        updated_at=now,
    )

    db.add(ticket)
    db.flush()

    creation_event = TicketEvent(
        ticket_id=ticket.id,
        author_id=creator_user_id.strip(),
        event_type=TicketEventType.TICKET_CREATED,
        content=f"Ticket '{ticket.title}' created with severity {ticket.severity.value}.",
        attachment_url=attachment_url,
        attachment_filename=stored_filename if attachment_url else None,
        created_at=now,
    )
    db.add(creation_event)
    db.commit()
    db.refresh(ticket)

    logger.info("Created support ticket %s (Org: %s, Severity: %s)", ticket.id, ticket.organization_id, ticket.severity)
    return ticket


def get_ticket_by_id(
    db: Session,
    ticket_id: str,
    user_organization_id: Optional[str] = None,
    for_update: bool = False,
) -> Ticket:
    """Retrieve support ticket by unique identifier with organization access validation.

    Args:
        db: Database session.
        ticket_id: Ticket UUID string.
        user_organization_id: Optional user organization context for boundary check.
        for_update: If True, acquires a row update lock on the query.

    Returns:
        Ticket ORM instance.

    Raises:
        TicketNotFoundError: If ticket does not exist.
        TicketPermissionDeniedError: If organization boundary check fails.
    """
    query = db.query(Ticket).filter(Ticket.id == ticket_id)
    if for_update:
        query = query.with_for_update()

    ticket = query.first()
    if not ticket:
        raise TicketNotFoundError(f"Support ticket with ID '{ticket_id}' was not found.")

    if user_organization_id:
        check_organization_access(ticket, user_organization_id)

    return ticket


def list_tickets(
    db: Session,
    organization_id: Optional[str] = None,
    status: Optional[Union[TicketStatus, str]] = None,
    severity: Optional[Union[TicketSeverity, str]] = None,
    assigned_agent_id: Optional[str] = None,
    creator_user_id: Optional[str] = None,
    is_sla_breached: Optional[bool] = None,
    page: int = 1,
    limit: int = 50,
) -> Tuple[List[Ticket], int]:
    """Query paginated list of support tickets filtered by criteria.

    Args:
        db: Database session.
        organization_id: Optional organization boundary filter.
        status: Optional status filter.
        severity: Optional severity filter.
        assigned_agent_id: Optional assigned agent ID filter.
        creator_user_id: Optional creator user ID filter.
        is_sla_breached: Optional SLA breach flag filter.
        page: Page number (1-based index).
        limit: Max items per page.

    Returns:
        Tuple containing (list of Ticket instances, total count).
    """
    query = db.query(Ticket)

    if organization_id and organization_id.strip().lower() not in {"*", "admin", "superadmin", "all"}:
        query = query.filter(Ticket.organization_id == organization_id.strip())

    if status:
        if isinstance(status, str):
            try:
                status = TicketStatus(status.upper())
            except ValueError:
                raise TicketValidationError(f"Invalid ticket status filter: '{status}'.")
        query = query.filter(Ticket.status == status)

    if severity:
        if isinstance(severity, str):
            try:
                severity = TicketSeverity(severity.upper())
            except ValueError:
                raise TicketValidationError(f"Invalid ticket severity filter: '{severity}'.")
        query = query.filter(Ticket.severity == severity)

    if assigned_agent_id:
        query = query.filter(Ticket.assigned_agent_id == assigned_agent_id.strip())

    if creator_user_id:
        query = query.filter(Ticket.creator_user_id == creator_user_id.strip())

    if is_sla_breached is not None:
        query = query.filter(Ticket.is_sla_breached == is_sla_breached)

    total_count = query.count()
    offset = max(0, (page - 1) * limit)
    tickets = query.order_by(Ticket.created_at.desc()).offset(offset).limit(limit).all()

    return tickets, total_count


def update_ticket_status(
    db: Session,
    ticket_id: str,
    author_id: str,
    new_status: Optional[Union[TicketStatus, str]] = None,
    new_severity: Optional[Union[TicketSeverity, str]] = None,
    assigned_agent_id: Optional[str] = None,
    comment: Optional[str] = None,
    user_organization_id: Optional[str] = None,
) -> Ticket:
    """Update support ticket status, severity, or assignment and record audit events.

    Handles dynamic SLA recalibration when severity is updated mid-lifecycle (EC-01).

    Args:
        db: Database session.
        ticket_id: Ticket UUID.
        author_id: User/agent ID performing the update.
        new_status: Target status state.
        new_severity: Reclassified severity state.
        assigned_agent_id: Agent ID to assign.
        comment: Optional change explanation comment.
        user_organization_id: Optional user organization context for boundary check.

    Returns:
        Updated Ticket instance.

    Raises:
        TicketNotFoundError: If ticket does not exist.
        TicketPermissionDeniedError: If organization boundary check fails.
        TicketValidationError: If status or severity parameters are invalid.
    """
    ticket = get_ticket_by_id(db, ticket_id, user_organization_id=user_organization_id, for_update=True)
    now = datetime.now(timezone.utc)

    # Handle mid-lifecycle severity reclassification (EC-01)
    if new_severity is not None:
        if isinstance(new_severity, str):
            try:
                new_severity = TicketSeverity(new_severity.upper())
            except ValueError:
                raise TicketValidationError(f"Invalid ticket severity classification: '{new_severity}'.")

        if new_severity != ticket.severity:
            old_severity = ticket.severity

            # Recalibrate response_due_at, resolution_due_at, and is_sla_breached dynamically from creation timestamp (EC-01)
            recalibrate_ticket_sla(ticket, new_severity, current_time=now)

            sev_event = TicketEvent(
                ticket_id=ticket.id,
                author_id=author_id,
                event_type=TicketEventType.STATUS_CHANGE,
                content=(
                    f"Severity reclassified from {old_severity.value} to {new_severity.value}. "
                    "SLA target timestamps dynamically recalibrated from creation time."
                ),
                created_at=now,
            )
            db.add(sev_event)

    # Handle status transition (FR-03)
    if new_status is not None:
        if isinstance(new_status, str):
            try:
                new_status = TicketStatus(new_status.upper())
            except ValueError:
                raise TicketValidationError(f"Invalid ticket status: '{new_status}'.")

        if new_status != ticket.status:
            old_status = ticket.status
            ticket.status = new_status

            if new_status in (TicketStatus.RESOLVED, TicketStatus.CLOSED) and ticket.resolved_at is None:
                ticket.resolved_at = now
            elif new_status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
                ticket.resolved_at = None

            status_event = TicketEvent(
                ticket_id=ticket.id,
                author_id=author_id,
                event_type=TicketEventType.STATUS_CHANGE,
                content=f"Status changed from {old_status.value} to {new_status.value}.",
                created_at=now,
            )
            db.add(status_event)

    # Handle agent assignment
    if assigned_agent_id is not None and assigned_agent_id != ticket.assigned_agent_id:
        old_agent = ticket.assigned_agent_id or "unassigned"
        ticket.assigned_agent_id = assigned_agent_id

        agent_event = TicketEvent(
            ticket_id=ticket.id,
            author_id=author_id,
            event_type=TicketEventType.STATUS_CHANGE,
            content=f"Assigned agent updated from '{old_agent}' to '{assigned_agent_id}'.",
            created_at=now,
        )
        db.add(agent_event)

    # Handle optional comment
    if comment and comment.strip():
        comment_event = TicketEvent(
            ticket_id=ticket.id,
            author_id=author_id,
            event_type=TicketEventType.COMMENT,
            content=comment.strip(),
            created_at=now,
        )
        db.add(comment_event)

    ticket.updated_at = now
    db.commit()
    db.refresh(ticket)

    logger.info("Updated ticket %s status=%s severity=%s assigned_agent=%s", ticket.id, ticket.status, ticket.severity, ticket.assigned_agent_id)
    return ticket


def add_ticket_event(
    db: Session,
    ticket_id: str,
    author_id: str,
    content: Optional[str] = None,
    attachment_filename: Optional[str] = None,
    attachment_content: Optional[bytes] = None,
    storage_port: Optional[AttachmentStoragePort] = None,
    event_type: Optional[TicketEventType] = None,
    user_organization_id: Optional[str] = None,
) -> TicketEvent:
    """Add a reply, comment, or attachment event to a ticket's chronological audit log.

    Handles concurrent reply insertion safely (EC-02).

    Args:
        db: Database session.
        ticket_id: Ticket UUID.
        author_id: Author user ID.
        content: Reply/comment text content.
        attachment_filename: Optional attachment filename.
        attachment_content: Optional raw attachment bytes.
        storage_port: Optional attachment storage adapter port.
        event_type: Optional explicit TicketEventType.
        user_organization_id: Optional user organization context for boundary check.

    Returns:
        Created TicketEvent instance.

    Raises:
        TicketNotFoundError: If ticket does not exist.
        TicketPermissionDeniedError: If organization boundary check fails.
        TicketValidationError: If neither content nor attachment is provided.
    """
    ticket = get_ticket_by_id(db, ticket_id, user_organization_id=user_organization_id, for_update=True)

    if not content and not attachment_content:
        raise TicketValidationError("A ticket event or reply must contain text content or a file attachment.")

    attachment_url = None
    stored_filename = None

    if attachment_filename and attachment_content:
        if storage_port is None:
            from src.connectors.storage import AttachmentStorageAdapter

            storage_port = AttachmentStorageAdapter()

        saved_meta = storage_port.save_attachment(
            filename=attachment_filename,
            content=attachment_content,
            ticket_id=ticket_id,
        )
        attachment_url = saved_meta.get("attachment_url")
        stored_filename = saved_meta.get("stored_filename", attachment_filename)

    if event_type is None:
        event_type = TicketEventType.ATTACHMENT_ADDED if attachment_url else TicketEventType.COMMENT

    now = datetime.now(timezone.utc)
    event = TicketEvent(
        ticket_id=ticket.id,
        author_id=author_id,
        event_type=event_type,
        content=content.strip() if content else None,
        attachment_url=attachment_url,
        attachment_filename=stored_filename if attachment_url else None,
        created_at=now,
    )

    ticket.updated_at = now
    db.add(event)
    db.commit()
    db.refresh(event)

    logger.info("Added audit log event %s to ticket %s (type=%s, author=%s)", event.id, ticket.id, event_type, author_id)
    return event


def get_ticket_events(
    db: Session,
    ticket_id: str,
    user_organization_id: Optional[str] = None,
) -> List[TicketEvent]:
    """Retrieve all chronological audit log events for a support ticket.

    Args:
        db: Database session.
        ticket_id: Ticket UUID.
        user_organization_id: Optional user organization context for boundary check.

    Returns:
        List of TicketEvent instances ordered chronologically.

    Raises:
        TicketNotFoundError: If ticket does not exist.
        TicketPermissionDeniedError: If organization boundary check fails.
    """
    ticket = get_ticket_by_id(db, ticket_id, user_organization_id=user_organization_id)
    return (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.created_at.asc())
        .all()
    )


def delete_ticket(
    db: Session,
    ticket_id: str,
    user_organization_id: Optional[str] = None,
) -> None:
    """Delete a support ticket and cascade associated events and CSAT records.

    Args:
        db: Database session.
        ticket_id: Ticket UUID.
        user_organization_id: Optional user organization context for boundary check.

    Raises:
        TicketNotFoundError: If ticket does not exist.
        TicketPermissionDeniedError: If organization boundary check fails.
    """
    ticket = get_ticket_by_id(db, ticket_id, user_organization_id=user_organization_id, for_update=True)
    db.delete(ticket)
    db.commit()
    logger.info("Deleted support ticket %s", ticket_id)
