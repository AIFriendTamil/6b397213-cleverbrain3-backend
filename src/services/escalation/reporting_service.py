"""Operational performance reporting service for support escalation and SLA tracking.

Computes Mean Time To Resolution (MTTR), SLA compliance percentages, and aggregate CSAT metrics.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from src.models.csat import CSATResponse
from src.models.ticket import Ticket, TicketSeverity, TicketStatus

logger = logging.getLogger(__name__)


def _ensure_utc(dt: Optional[Union[datetime, str]]) -> Optional[datetime]:
    """Ensure timestamp parameter is parsed, timezone-aware, and set to UTC.

    Args:
        dt: Optional datetime object or ISO string.

    Returns:
        Timezone-aware UTC datetime or None.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        cleaned = dt.strip()
        if not cleaned:
            return None
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_duration(seconds: float) -> str:
    """Format duration in seconds into human-readable representation.

    Args:
        seconds: Time interval in seconds.

    Returns:
        Formatted string (e.g. '1d 4h 30m', '3h 15m', or '45m').
    """
    if seconds <= 0:
        return "0m"
    total_minutes = int(seconds // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    days = hours // 24
    rem_hours = hours % 24

    if days > 0:
        return f"{days}d {rem_hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@dataclass
class CSATMetrics:
    """Dataclass holding aggregate CSAT survey ratings and satisfaction scores."""

    total_responses: int
    average_rating: float
    satisfied_count: int
    satisfaction_percentage: float
    rating_distribution: Dict[int, int]

    def to_dict(self) -> Dict[str, Any]:
        """Convert CSAT metrics to dictionary representation."""
        return asdict(self)


@dataclass
class SLAMetrics:
    """Dataclass holding SLA compliance statistics."""

    total_tickets: int
    compliant_tickets: int
    breached_tickets: int
    compliance_percentage: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert SLA metrics to dictionary representation."""
        return asdict(self)


@dataclass
class MTTRMetrics:
    """Dataclass holding Mean Time To Resolution (MTTR) metrics."""

    total_resolved_tickets: int
    average_seconds: float
    average_hours: float
    formatted_mttr: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert MTTR metrics to dictionary representation."""
        return asdict(self)


@dataclass
class PerformanceReport:
    """Dataclass holding aggregate operational performance report metrics."""

    start_time: Optional[str]
    end_time: Optional[str]
    organization_id: Optional[str]
    total_tickets: int
    open_tickets: int
    resolved_tickets: int
    mttr: MTTRMetrics
    sla_compliance: SLAMetrics
    csat: CSATMetrics
    severity_breakdown: Dict[str, Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert performance report object to dictionary representation."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "organization_id": self.organization_id,
            "total_tickets": self.total_tickets,
            "open_tickets": self.open_tickets,
            "resolved_tickets": self.resolved_tickets,
            "mttr": self.mttr.to_dict(),
            "sla_compliance": self.sla_compliance.to_dict(),
            "csat": self.csat.to_dict(),
            "severity_breakdown": self.severity_breakdown,
        }


def _filter_tickets_query(
    db: Session,
    organization_id: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    severity: Optional[Union[TicketSeverity, str]] = None,
):
    """Build filtered SQLAlchemy query for Ticket entities based on parameters.

    Args:
        db: Database session.
        organization_id: Optional organization filter.
        start_time: Optional start timestamp filter.
        end_time: Optional end timestamp filter.
        severity: Optional severity filter.

    Returns:
        Filtered SQLAlchemy Query instance.
    """
    query = db.query(Ticket)

    if organization_id and organization_id.strip().lower() not in {
        "*",
        "admin",
        "superadmin",
        "all",
    }:
        query = query.filter(Ticket.organization_id == organization_id.strip())

    if start_time is not None:
        query = query.filter(Ticket.created_at >= start_time)

    if end_time is not None:
        query = query.filter(Ticket.created_at <= end_time)

    if severity is not None:
        if isinstance(severity, str):
            severity = TicketSeverity(severity.strip().upper())
        query = query.filter(Ticket.severity == severity)

    return query


def compute_mttr(tickets: List[Ticket]) -> MTTRMetrics:
    """Calculate Mean Time To Resolution (MTTR) across resolved tickets.

    Args:
        tickets: List of Ticket entities.

    Returns:
        MTTRMetrics dataclass instance.
    """
    resolved_tickets = [
        t
        for t in tickets
        if t.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        or t.resolved_at is not None
    ]

    if not resolved_tickets:
        return MTTRMetrics(
            total_resolved_tickets=0,
            average_seconds=0.0,
            average_hours=0.0,
            formatted_mttr="0m",
        )

    total_seconds = 0.0
    valid_count = 0

    for ticket in resolved_tickets:
        end_timestamp = ticket.resolved_at or ticket.updated_at
        if end_timestamp and ticket.created_at:
            start_utc = _ensure_utc(ticket.created_at)
            end_utc = _ensure_utc(end_timestamp)
            if start_utc and end_utc:
                duration = (end_utc - start_utc).total_seconds()
                if duration >= 0:
                    total_seconds += duration
                    valid_count += 1

    if valid_count == 0:
        return MTTRMetrics(
            total_resolved_tickets=0,
            average_seconds=0.0,
            average_hours=0.0,
            formatted_mttr="0m",
        )

    avg_seconds = round(total_seconds / valid_count, 2)
    avg_hours = round(avg_seconds / 3600.0, 2)
    formatted = _format_duration(avg_seconds)

    return MTTRMetrics(
        total_resolved_tickets=valid_count,
        average_seconds=avg_seconds,
        average_hours=avg_hours,
        formatted_mttr=formatted,
    )


def compute_sla_compliance(tickets: List[Ticket]) -> SLAMetrics:
    """Calculate SLA compliance statistics across a list of tickets.

    Args:
        tickets: List of Ticket entities.

    Returns:
        SLAMetrics dataclass instance.
    """
    total = len(tickets)
    if total == 0:
        return SLAMetrics(
            total_tickets=0,
            compliant_tickets=0,
            breached_tickets=0,
            compliance_percentage=100.0,
        )

    breached = sum(1 for t in tickets if t.is_sla_breached)
    compliant = total - breached
    percentage = round((compliant / total) * 100.0, 2)

    return SLAMetrics(
        total_tickets=total,
        compliant_tickets=compliant,
        breached_tickets=breached,
        compliance_percentage=percentage,
    )


def compute_csat_metrics(db: Session, ticket_ids: List[str]) -> CSATMetrics:
    """Calculate aggregate CSAT ratings and satisfaction rates for specified tickets.

    Args:
        db: Database session.
        ticket_ids: List of ticket UUIDs.

    Returns:
        CSATMetrics dataclass instance.
    """
    distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    if not ticket_ids:
        return CSATMetrics(
            total_responses=0,
            average_rating=0.0,
            satisfied_count=0,
            satisfaction_percentage=0.0,
            rating_distribution=distribution,
        )

    responses = (
        db.query(CSATResponse).filter(CSATResponse.ticket_id.in_(ticket_ids)).all()
    )

    total_responses = len(responses)
    if total_responses == 0:
        return CSATMetrics(
            total_responses=0,
            average_rating=0.0,
            satisfied_count=0,
            satisfaction_percentage=0.0,
            rating_distribution=distribution,
        )

    rating_sum = 0
    satisfied_count = 0

    for resp in responses:
        rating = resp.rating
        if rating in distribution:
            distribution[rating] += 1
        rating_sum += rating
        if rating >= 4:
            satisfied_count += 1

    avg_rating = round(rating_sum / total_responses, 2)
    satisfaction_pct = round((satisfied_count / total_responses) * 100.0, 2)

    return CSATMetrics(
        total_responses=total_responses,
        average_rating=avg_rating,
        satisfied_count=satisfied_count,
        satisfaction_percentage=satisfaction_pct,
        rating_distribution=distribution,
    )


def _compute_severity_breakdown(
    db: Session,
    tickets: List[Ticket],
) -> Dict[str, Dict[str, Any]]:
    """Compute performance metrics broken down by severity level (P1, P2, P3).

    Args:
        db: Database session.
        tickets: List of Ticket entities.

    Returns:
        Dict mapping severity string to performance metrics dict.
    """
    breakdown = {}

    for sev in (TicketSeverity.P1, TicketSeverity.P2, TicketSeverity.P3):
        sev_tickets = [t for t in tickets if t.severity == sev]
        sev_ticket_ids = [t.id for t in sev_tickets]

        mttr = compute_mttr(sev_tickets)
        sla = compute_sla_compliance(sev_tickets)
        csat = compute_csat_metrics(db, sev_ticket_ids)

        breakdown[sev.value] = {
            "total_tickets": len(sev_tickets),
            "mttr": mttr.to_dict(),
            "sla_compliance": sla.to_dict(),
            "csat": csat.to_dict(),
        }

    return breakdown


def generate_performance_report(
    db: Session,
    start_time: Optional[Union[datetime, str]] = None,
    end_time: Optional[Union[datetime, str]] = None,
    organization_id: Optional[str] = None,
    severity: Optional[Union[TicketSeverity, str]] = None,
) -> PerformanceReport:
    """Generate operational performance report metrics across support tickets and CSAT responses.

    Args:
        db: Database session.
        start_time: Optional start cutoff timestamp.
        end_time: Optional end cutoff timestamp.
        organization_id: Optional organization boundary filter.
        severity: Optional severity filter.

    Returns:
        PerformanceReport object containing MTTR, SLA %, CSAT stats, and severity breakdown.
    """
    start_utc = _ensure_utc(start_time)
    end_utc = _ensure_utc(end_time)

    query = _filter_tickets_query(
        db=db,
        organization_id=organization_id,
        start_time=start_utc,
        end_time=end_utc,
        severity=severity,
    )

    tickets = query.all()
    ticket_ids = [t.id for t in tickets]

    total_tickets = len(tickets)
    resolved_count = sum(
        1
        for t in tickets
        if t.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        or t.resolved_at is not None
    )
    open_count = total_tickets - resolved_count

    mttr_metrics = compute_mttr(tickets)
    sla_metrics = compute_sla_compliance(tickets)
    csat_metrics = compute_csat_metrics(db, ticket_ids)
    severity_breakdown = _compute_severity_breakdown(db, tickets)

    start_str = start_utc.isoformat() if start_utc else None
    end_str = end_utc.isoformat() if end_utc else None

    logger.info(
        "Generated performance report: total_tickets=%d, resolved=%d, mttr_hours=%.2f, "
        "sla_compliance=%.2f%%, csat_avg=%.2f",
        total_tickets,
        resolved_count,
        mttr_metrics.average_hours,
        sla_metrics.compliance_percentage,
        csat_metrics.average_rating,
    )

    return PerformanceReport(
        start_time=start_str,
        end_time=end_str,
        organization_id=organization_id,
        total_tickets=total_tickets,
        open_tickets=open_count,
        resolved_tickets=resolved_count,
        mttr=mttr_metrics,
        sla_compliance=sla_metrics,
        csat=csat_metrics,
        severity_breakdown=severity_breakdown,
    )
