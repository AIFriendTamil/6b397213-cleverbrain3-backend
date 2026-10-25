"""SLA calculation engine for target deadlines, breach evaluation, countdown timers, and dynamic recalibration."""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, Union

from src.lib.config import settings
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus

logger = logging.getLogger(__name__)


@dataclass
class SLACountdownInfo:
    """Dataclass holding detailed SLA countdown, warning, and breach evaluation metrics."""

    ticket_id: str
    severity: str
    status: str
    response_due_at: str
    resolution_due_at: str
    response_time_remaining_seconds: float
    resolution_time_remaining_seconds: float
    response_percent_elapsed: float
    resolution_percent_elapsed: float
    response_warning_threshold_percent: float
    resolution_warning_threshold_percent: float
    is_response_warning: bool
    is_resolution_warning: bool
    is_response_breached: bool
    is_resolution_breached: bool
    is_warning: bool
    is_breached: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert SLA countdown metric object to dictionary representation."""
        return asdict(self)


def _ensure_utc(dt: Optional[datetime]) -> datetime:
    """Ensure timestamp is timezone-aware and set to UTC timezone.

    Args:
        dt: Optional datetime object.

    Returns:
        Timezone-aware UTC datetime object (defaults to current UTC timestamp if None).
    """
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_severity(severity: Union[TicketSeverity, str]) -> TicketSeverity:
    """Parse and validate ticket severity parameter into TicketSeverity enum.

    Args:
        severity: TicketSeverity enum value or string representation.

    Returns:
        Parsed TicketSeverity enum instance.

    Raises:
        ValueError: If severity parameter is invalid.
    """
    if isinstance(severity, TicketSeverity):
        return severity
    if isinstance(severity, str):
        cleaned = severity.strip().upper()
        try:
            return TicketSeverity(cleaned)
        except ValueError:
            raise ValueError(f"Invalid ticket severity classification: '{severity}'.")
    raise ValueError(f"Unsupported severity type: {type(severity)}.")


def get_sla_threshold_settings(severity: Union[TicketSeverity, str]) -> Dict[str, float]:
    """Retrieve SLA response hours, resolution hours, and warning thresholds for a severity.

    Args:
        severity: Incident severity classification (P1, P2, P3).

    Returns:
        Dict containing response_hours, resolution_hours, and warning_threshold_percent.
    """
    sev_enum = _parse_severity(severity)
    if sev_enum == TicketSeverity.P1:
        return {
            "response_hours": float(settings.SLA_P1_RESPONSE_HOURS),
            "resolution_hours": float(settings.SLA_P1_RESOLUTION_HOURS),
            "warning_threshold_percent": float(settings.SLA_P1_WARNING_THRESHOLD_PERCENT),
        }
    elif sev_enum == TicketSeverity.P2:
        return {
            "response_hours": float(settings.SLA_P2_RESPONSE_HOURS),
            "resolution_hours": float(settings.SLA_P2_RESOLUTION_HOURS),
            "warning_threshold_percent": float(settings.SLA_P2_WARNING_THRESHOLD_PERCENT),
        }
    else:
        return {
            "response_hours": float(settings.SLA_P3_RESPONSE_HOURS),
            "resolution_hours": float(settings.SLA_P3_RESOLUTION_HOURS),
            "warning_threshold_percent": float(settings.SLA_P3_WARNING_THRESHOLD_PERCENT),
        }


def calculate_sla_due_dates(
    severity: Union[TicketSeverity, str],
    created_at: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """Calculate response and resolution target due timestamps based on ticket severity.

    Args:
        severity: Incident severity classification (P1, P2, P3).
        created_at: Reference creation timestamp (defaults to UTC now).

    Returns:
        Tuple containing (response_due_at, resolution_due_at).
    """
    ref_time = _ensure_utc(created_at)
    thresholds = get_sla_threshold_settings(severity)

    response_due_at = ref_time + timedelta(hours=thresholds["response_hours"])
    resolution_due_at = ref_time + timedelta(hours=thresholds["resolution_hours"])

    return response_due_at, resolution_due_at


def calculate_target_deadlines(
    severity: Union[TicketSeverity, str],
    created_at: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """Alias for calculate_sla_due_dates computing SLA target deadlines."""
    return calculate_sla_due_dates(severity, created_at=created_at)


def _calculate_percentage_elapsed(
    created_at: datetime, due_at: datetime, current_time: datetime
) -> float:
    """Compute percentage of total SLA duration that has elapsed.

    Args:
        created_at: SLA start timestamp.
        due_at: SLA target deadline.
        current_time: Evaluation timestamp.

    Returns:
        Float percentage value (0.0 to 100.0+).
    """
    total_seconds = (due_at - created_at).total_seconds()
    if total_seconds <= 0:
        return 100.0 if current_time >= due_at else 0.0

    elapsed_seconds = (current_time - created_at).total_seconds()
    if elapsed_seconds <= 0:
        return 0.0

    return (elapsed_seconds / total_seconds) * 100.0


def evaluate_ticket_breach(
    ticket: Ticket,
    current_time: Optional[datetime] = None,
) -> bool:
    """Evaluate whether ticket has breached its response or resolution SLA targets.

    Args:
        ticket: Ticket instance to evaluate.
        current_time: Reference timestamp for evaluation.

    Returns:
        True if either response SLA or resolution SLA is breached.
    """
    now = _ensure_utc(current_time)
    resp_due = _ensure_utc(ticket.response_due_at)
    res_due = _ensure_utc(ticket.resolution_due_at)

    has_responded = (
        ticket.status != TicketStatus.NEW
        or ticket.assigned_agent_id is not None
    )
    is_response_breached = not has_responded and now > resp_due

    has_resolved = (
        ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        or ticket.resolved_at is not None
    )
    if has_resolved and ticket.resolved_at is not None:
        resolved_time = _ensure_utc(ticket.resolved_at)
        is_resolution_breached = resolved_time > res_due
    else:
        is_resolution_breached = not has_resolved and now > res_due

    return bool(ticket.is_sla_breached or is_response_breached or is_resolution_breached)


def evaluate_sla_status(
    ticket: Ticket,
    current_time: Optional[datetime] = None,
) -> SLACountdownInfo:
    """Evaluate detailed real-time SLA countdown, warning state, and breach metrics.

    Args:
        ticket: Ticket instance.
        current_time: Reference evaluation timestamp.

    Returns:
        SLACountdownInfo dataclass instance.
    """
    now = _ensure_utc(current_time)
    created_at = _ensure_utc(ticket.created_at)
    resp_due = _ensure_utc(ticket.response_due_at)
    res_due = _ensure_utc(ticket.resolution_due_at)

    thresholds = get_sla_threshold_settings(ticket.severity)
    warn_threshold = thresholds["warning_threshold_percent"]

    resp_remaining = (resp_due - now).total_seconds()
    res_remaining = (res_due - now).total_seconds()

    resp_pct = _calculate_percentage_elapsed(created_at, resp_due, now)
    res_pct = _calculate_percentage_elapsed(created_at, res_due, now)

    has_responded = (
        ticket.status != TicketStatus.NEW
        or ticket.assigned_agent_id is not None
    )
    has_resolved = (
        ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        or ticket.resolved_at is not None
    )

    is_resp_breached = not has_responded and now > resp_due
    if has_resolved and ticket.resolved_at is not None:
        is_res_breached = _ensure_utc(ticket.resolved_at) > res_due
    else:
        is_res_breached = not has_resolved and now > res_due

    is_resp_warning = (
        not has_responded
        and not is_resp_breached
        and resp_pct >= warn_threshold
    )
    is_res_warning = (
        not has_resolved
        and not is_res_breached
        and res_pct >= warn_threshold
    )

    is_breached = bool(ticket.is_sla_breached or is_resp_breached or is_res_breached)
    is_warning = (is_resp_warning or is_res_warning) and not is_breached

    return SLACountdownInfo(
        ticket_id=ticket.id,
        severity=ticket.severity.value if isinstance(ticket.severity, TicketSeverity) else str(ticket.severity),
        status=ticket.status.value if isinstance(ticket.status, TicketStatus) else str(ticket.status),
        response_due_at=resp_due.isoformat(),
        resolution_due_at=res_due.isoformat(),
        response_time_remaining_seconds=resp_remaining,
        resolution_time_remaining_seconds=res_remaining,
        response_percent_elapsed=round(resp_pct, 2),
        resolution_percent_elapsed=round(res_pct, 2),
        response_warning_threshold_percent=warn_threshold,
        resolution_warning_threshold_percent=warn_threshold,
        is_response_warning=is_resp_warning,
        is_resolution_warning=is_res_warning,
        is_response_breached=is_resp_breached,
        is_resolution_breached=is_res_breached,
        is_warning=is_warning,
        is_breached=is_breached,
    )


def recalibrate_sla_deadlines(
    ticket: Ticket,
    new_severity: Union[TicketSeverity, str],
    current_time: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """Recalibrate response and resolution SLA target deadlines for a ticket (EC-01).

    Calculates new due target timestamps from original ticket creation time.

    Args:
        ticket: Target Ticket instance.
        new_severity: Reclassified TicketSeverity.
        current_time: Reference evaluation timestamp.

    Returns:
        Tuple containing (new_response_due_at, new_resolution_due_at).
    """
    sev_enum = _parse_severity(new_severity)
    created_at = _ensure_utc(ticket.created_at)
    return calculate_sla_due_dates(sev_enum, created_at=created_at)


def recalibrate_ticket_sla(
    ticket: Ticket,
    new_severity: Union[TicketSeverity, str],
    current_time: Optional[datetime] = None,
) -> Ticket:
    """Recalibrate ticket SLA deadlines, update severity, and re-evaluate breach status (EC-01).

    Args:
        ticket: Ticket instance to modify.
        new_severity: New severity classification (P1, P2, P3).
        current_time: Reference timestamp for breach evaluation.

    Returns:
        Updated Ticket instance.
    """
    sev_enum = _parse_severity(new_severity)
    now = _ensure_utc(current_time)

    new_resp_due, new_res_due = recalibrate_sla_deadlines(ticket, sev_enum, current_time=now)

    ticket.severity = sev_enum
    ticket.response_due_at = new_resp_due
    ticket.resolution_due_at = new_res_due
    ticket.is_sla_breached = evaluate_ticket_breach(ticket, current_time=now)

    logger.info(
        f"Recalibrated SLA for ticket {ticket.id} to {sev_enum.value}: "
        f"response_due={new_resp_due.isoformat()}, resolution_due={new_res_due.isoformat()}, "
        f"is_sla_breached={ticket.is_sla_breached}"
    )

    return ticket


def get_next_escalation_tier(current_tier: Union[EscalationTier, int]) -> Optional[int]:
    """Determine the next escalation tier on the predefined escalation path (FR-06).

    Args:
        current_tier: Current escalation tier integer or EscalationTier enum.

    Returns:
        Next tier integer value, or None if top tier (TIER_3) has been reached.
    """
    tier_val = int(current_tier)
    if tier_val < int(EscalationTier.TIER_2):
        return int(EscalationTier.TIER_2)
    elif tier_val < int(EscalationTier.TIER_3):
        return int(EscalationTier.TIER_3)
    return None


def should_escalate_ticket(
    ticket: Ticket,
    current_time: Optional[datetime] = None,
) -> bool:
    """Determine if a ticket should be escalated based on SLA breach or expiration.

    Args:
        ticket: Ticket instance.
        current_time: Evaluation timestamp.

    Returns:
        True if ticket is open and SLA targets are breached.
    """
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        return False

    return evaluate_ticket_breach(ticket, current_time=current_time)


def evaluate_escalation_path(
    ticket: Ticket,
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluate escalation path decisions for a ticket including emergency fallbacks (EC-04).

    Args:
        ticket: Ticket instance.
        current_time: Reference evaluation timestamp.

    Returns:
        Dict containing escalation evaluation flags, current/next tier, and fallback triggers.
    """
    now = _ensure_utc(current_time)
    should_esc = should_escalate_ticket(ticket, current_time=now)

    current_tier_val = int(ticket.escalation_tier)
    next_tier_val = get_next_escalation_tier(current_tier_val)
    is_top_tier = current_tier_val >= int(EscalationTier.TIER_3) or next_tier_val is None

    requires_emergency_fallback = should_esc and is_top_tier

    if should_esc:
        if requires_emergency_fallback:
            reason = (
                f"Ticket {ticket.id} breached SLA targets while at top tier "
                f"(Tier {current_tier_val}). Emergency fallback notification required."
            )
        else:
            reason = (
                f"Ticket {ticket.id} breached SLA targets. "
                f"Advancing escalation path from Tier {current_tier_val} to Tier {next_tier_val}."
            )
    else:
        reason = f"Ticket {ticket.id} is within SLA targets or resolved. No escalation required."

    return {
        "ticket_id": ticket.id,
        "should_escalate": should_esc,
        "current_tier": current_tier_val,
        "next_tier": next_tier_val,
        "is_top_tier_reached": is_top_tier,
        "requires_emergency_fallback": requires_emergency_fallback,
        "reason": reason,
    }
