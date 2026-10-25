"""Celery background worker tasks for periodic SLA breach tracking, escalation path progression, and emergency fallback alerts."""

from datetime import datetime, timezone
import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from src.lib.logging import clear_trace_id, set_trace_id
from src.models.audit_log import TicketEvent, TicketEventType
from src.models.base import SessionLocal
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus
from src.services.escalation.sla_engine import (
    evaluate_escalation_path,
    evaluate_ticket_breach,
)
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _parse_reference_time(reference_time_str: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Parse reference time parameter into a timezone-aware UTC datetime instance.

    Args:
        reference_time_str: Datetime instance or ISO formatted timestamp string.

    Returns:
        Timezone-aware UTC datetime instance, or None if input is None.

    Raises:
        ValueError: If timestamp string format cannot be parsed.
    """
    if reference_time_str is None:
        return None
    if isinstance(reference_time_str, datetime):
        if reference_time_str.tzinfo is None:
            return reference_time_str.replace(tzinfo=timezone.utc)
        return reference_time_str.astimezone(timezone.utc)

    if isinstance(reference_time_str, str) and reference_time_str.strip():
        s = reference_time_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                raise ValueError(
                    f"Invalid datetime string format: '{reference_time_str}'. Expected ISO 8601 format."
                )
    return None


@celery_app.task(name="src.workers.sla_tasks.evaluate_sla_breaches")
def evaluate_sla_breaches(
    reference_time_str: Optional[Union[str, datetime]] = None,
    organization_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Background Celery task to monitor SLA breaches, progress escalation tiers, and trigger emergency fallbacks.

    Scans active support tickets, evaluates target deadlines against reference time, flags SLA breaches (FR-05),
    advances escalation path tiers (FR-06), and triggers emergency fallback alerts when tickets breach SLA
    targets while at the top escalation tier (EC-04).

    Args:
        reference_time_str: Optional reference timestamp (datetime or ISO string) for evaluation.
        organization_id: Optional organization ID filter to constrain evaluation scope.
        correlation_id: Optional trace/correlation ID string for log context tracing.
        db_session: Optional pre-existing synchronous SQLAlchemy database session.

    Returns:
        Summary dictionary containing task status, correlation ID, evaluated tickets count,
        breached tickets count, newly escalated count, emergency fallback alerts count,
        and details lists for escalations and emergency fallbacks.
    """
    task_cid = correlation_id or str(uuid.uuid4())
    set_trace_id(task_cid)

    try:
        ref_time = _parse_reference_time(reference_time_str)
    except ValueError as val_err:
        logger.error(
            "Invalid datetime string format passed to evaluate_sla_breaches: %s",
            reference_time_str,
            extra={"correlation_id": task_cid},
        )
        clear_trace_id()
        return {
            "status": "failed",
            "error": str(val_err),
            "correlation_id": task_cid,
        }

    evaluation_time = ref_time or datetime.now(timezone.utc)
    evaluation_time_iso = evaluation_time.isoformat()

    logger.info(
        "Starting background SLA breach evaluation and escalation worker task",
        extra={
            "correlation_id": task_cid,
            "evaluation_time": evaluation_time_iso,
            "organization_id": organization_id,
        },
    )

    owns_session = False
    session = db_session
    if session is None:
        session = SessionLocal()
        owns_session = True

    try:
        # Query open support tickets (exclude RESOLVED and CLOSED)
        query = session.query(Ticket).filter(
            Ticket.status.notin_([TicketStatus.RESOLVED, TicketStatus.CLOSED])
        )
        if organization_id and organization_id.strip():
            query = query.filter(Ticket.organization_id == organization_id.strip())

        open_tickets = query.order_by(Ticket.created_at.asc()).all()

        evaluated_tickets_count = len(open_tickets)
        breached_tickets_count = 0
        newly_escalated_count = 0
        emergency_fallback_alerts_count = 0
        escalations: List[Dict[str, Any]] = []
        fallback_alerts: List[Dict[str, Any]] = []

        for ticket in open_tickets:
            is_breached = evaluate_ticket_breach(ticket, current_time=evaluation_time)

            if not is_breached and not ticket.is_sla_breached:
                continue

            # Flag ticket as breached if not already set (FR-05)
            breached_tickets_count += 1
            if not ticket.is_sla_breached:
                ticket.is_sla_breached = True

            esc_info = evaluate_escalation_path(ticket, current_time=evaluation_time)

            if esc_info["requires_emergency_fallback"]:
                # EC-04: Emergency fallback notification when ticket breaches SLA at top tier
                existing_fallback = (
                    session.query(TicketEvent)
                    .filter(
                        TicketEvent.ticket_id == ticket.id,
                        TicketEvent.event_type == TicketEventType.ESCALATION_TRIGGERED,
                        TicketEvent.content.like("%EMERGENCY FALLBACK%"),
                    )
                    .first()
                )

                fallback_entry = {
                    "ticket_id": ticket.id,
                    "organization_id": ticket.organization_id,
                    "severity": ticket.severity.value if isinstance(ticket.severity, TicketSeverity) else str(ticket.severity),
                    "escalation_tier": int(ticket.escalation_tier),
                    "status": ticket.status.value if isinstance(ticket.status, TicketStatus) else str(ticket.status),
                    "reason": esc_info["reason"],
                    "evaluated_at": evaluation_time_iso,
                }
                fallback_alerts.append(fallback_entry)
                emergency_fallback_alerts_count += 1

                if not existing_fallback:
                    fallback_event = TicketEvent(
                        ticket_id=ticket.id,
                        author_id="system_sla_worker",
                        event_type=TicketEventType.ESCALATION_TRIGGERED,
                        content=(
                            f"EMERGENCY FALLBACK ALERT: Ticket '{ticket.title}' breached SLA targets "
                            f"while at top escalation tier (Tier {ticket.escalation_tier}). "
                            "Immediate executive intervention required."
                        ),
                        created_at=evaluation_time,
                    )
                    session.add(fallback_event)

                logger.warning(
                    "Emergency fallback alert triggered for ticket %s (Org: %s, Tier: %d)",
                    ticket.id,
                    ticket.organization_id,
                    ticket.escalation_tier,
                    extra={
                        "correlation_id": task_cid,
                        "ticket_id": ticket.id,
                        "organization_id": ticket.organization_id,
                    },
                )

            elif esc_info["should_escalate"] and esc_info["next_tier"] is not None:
                # FR-06: Predefined escalation path progression
                old_tier = int(ticket.escalation_tier)
                new_tier = esc_info["next_tier"]
                old_status = ticket.status

                ticket.escalation_tier = new_tier
                if ticket.status in (TicketStatus.NEW, TicketStatus.IN_PROGRESS):
                    ticket.status = TicketStatus.ESCALATED

                ticket.updated_at = evaluation_time

                escalation_event = TicketEvent(
                    ticket_id=ticket.id,
                    author_id="system_sla_worker",
                    event_type=TicketEventType.ESCALATION_TRIGGERED,
                    content=(
                        f"SLA target breached. Escalation path advanced from Tier {old_tier} "
                        f"to Tier {new_tier}. Status updated from {old_status.value} to {ticket.status.value}."
                    ),
                    created_at=evaluation_time,
                )
                session.add(escalation_event)

                newly_escalated_count += 1
                escalations.append({
                    "ticket_id": ticket.id,
                    "organization_id": ticket.organization_id,
                    "previous_tier": old_tier,
                    "new_tier": new_tier,
                    "previous_status": old_status.value if isinstance(old_status, TicketStatus) else str(old_status),
                    "new_status": ticket.status.value if isinstance(ticket.status, TicketStatus) else str(ticket.status),
                    "evaluated_at": evaluation_time_iso,
                })

                logger.info(
                    "Escalated ticket %s from Tier %d to Tier %d (Status: %s)",
                    ticket.id,
                    old_tier,
                    new_tier,
                    ticket.status,
                    extra={
                        "correlation_id": task_cid,
                        "ticket_id": ticket.id,
                        "new_tier": new_tier,
                    },
                )

        session.commit()

        summary = {
            "status": "completed",
            "correlation_id": task_cid,
            "evaluated_tickets_count": evaluated_tickets_count,
            "breached_tickets_count": breached_tickets_count,
            "newly_escalated_count": newly_escalated_count,
            "emergency_fallback_alerts_count": emergency_fallback_alerts_count,
            "escalations": escalations,
            "fallback_alerts": fallback_alerts,
            "evaluated_at": evaluation_time_iso,
        }

        logger.info(
            "Completed SLA breach tracking task: evaluated %d tickets, %d breached, %d escalated, %d emergency fallback alerts",
            evaluated_tickets_count,
            breached_tickets_count,
            newly_escalated_count,
            emergency_fallback_alerts_count,
            extra={"correlation_id": task_cid},
        )

        return summary

    except Exception as exc:
        if session is not None:
            session.rollback()
        logger.exception(
            "Error during SLA breach evaluation task execution: %s",
            str(exc),
            extra={"correlation_id": task_cid},
        )
        return {
            "status": "failed",
            "error": str(exc),
            "correlation_id": task_cid,
        }
    finally:
        if owns_session and session is not None:
            session.close()
        clear_trace_id()


@celery_app.task(name="src.workers.sla_tasks.check_sla_breaches")
def check_sla_breaches(
    reference_time_str: Optional[Union[str, datetime]] = None,
    organization_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Alias Celery task wrapper invoking evaluate_sla_breaches."""
    return evaluate_sla_breaches(
        reference_time_str=reference_time_str,
        organization_id=organization_id,
        correlation_id=correlation_id,
        db_session=db_session,
    )
