"""Unit tests for performance reporting service computing MTTR, SLA compliance, and CSAT metrics."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.csat import CSATResponse
from src.models.ticket import Ticket, TicketSeverity, TicketStatus
from src.services.escalation.reporting_service import (
    CSATMetrics,
    MTTRMetrics,
    PerformanceReport,
    SLAMetrics,
    _format_duration,
    compute_csat_metrics,
    compute_mttr,
    compute_sla_compliance,
    generate_performance_report,
)


@pytest.fixture
def db_session():
    """Fixture providing an ephemeral in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()


def test_format_duration_helper():
    """Verify duration formatting into days, hours, and minutes."""
    assert _format_duration(0) == "0m"
    assert _format_duration(-10) == "0m"
    assert _format_duration(45 * 60) == "45m"
    assert _format_duration(2 * 3600 + 15 * 60) == "2h 15m"
    assert _format_duration(26 * 3600 + 30 * 60) == "1d 2h 30m"


def test_compute_mttr_empty():
    """Verify MTTR calculation handles empty ticket lists gracefully."""
    metrics = compute_mttr([])
    assert metrics.total_resolved_tickets == 0
    assert metrics.average_seconds == 0.0
    assert metrics.average_hours == 0.0
    assert metrics.formatted_mttr == "0m"


def test_compute_mttr_with_resolved_tickets():
    """Verify MTTR average seconds and hours across resolved tickets."""
    now = datetime.now(timezone.utc)
    t1 = Ticket(
        id="t1",
        title="Ticket 1",
        description="Desc",
        severity=TicketSeverity.P1,
        status=TicketStatus.RESOLVED,
        organization_id="org1",
        creator_user_id="u1",
        response_due_at=now,
        resolution_due_at=now,
        created_at=now - timedelta(hours=2),
        resolved_at=now,
    )
    t2 = Ticket(
        id="t2",
        title="Ticket 2",
        description="Desc",
        severity=TicketSeverity.P2,
        status=TicketStatus.CLOSED,
        organization_id="org1",
        creator_user_id="u1",
        response_due_at=now,
        resolution_due_at=now,
        created_at=now - timedelta(hours=4),
        resolved_at=now,
    )
    t3 = Ticket(
        id="t3",
        title="Ticket 3",
        description="Desc",
        severity=TicketSeverity.P3,
        status=TicketStatus.IN_PROGRESS,
        organization_id="org1",
        creator_user_id="u1",
        response_due_at=now,
        resolution_due_at=now,
        created_at=now - timedelta(hours=1),
        resolved_at=None,
    )

    metrics = compute_mttr([t1, t2, t3])
    assert metrics.total_resolved_tickets == 2
    # Avg of 2h (7200s) and 4h (14400s) = 3h (10800s)
    assert metrics.average_seconds == 10800.0
    assert metrics.average_hours == 3.0
    assert metrics.formatted_mttr == "3h 0m"


def test_compute_sla_compliance_metrics():
    """Verify SLA compliance percentage calculation."""
    # Empty case
    empty_sla = compute_sla_compliance([])
    assert empty_sla.total_tickets == 0
    assert empty_sla.compliance_percentage == 100.0

    now = datetime.now(timezone.utc)
    tickets = [
        Ticket(
            id=f"t{i}",
            title=f"T{i}",
            description="Desc",
            severity=TicketSeverity.P1,
            status=TicketStatus.NEW,
            organization_id="org1",
            creator_user_id="u1",
            response_due_at=now,
            resolution_due_at=now,
            is_sla_breached=(i == 1),  # 1 out of 4 breached
        )
        for i in range(4)
    ]

    sla = compute_sla_compliance(tickets)
    assert sla.total_tickets == 4
    assert sla.breached_tickets == 1
    assert sla.compliant_tickets == 3
    assert sla.compliance_percentage == 75.0


def test_compute_csat_metrics_with_db(db_session: Session):
    """Verify CSAT rating distribution, average, and satisfaction percentage."""
    now = datetime.now(timezone.utc)

    # Empty case
    csat_empty = compute_csat_metrics(db_session, [])
    assert csat_empty.total_responses == 0
    assert csat_empty.average_rating == 0.0

    # Populate CSAT responses
    c1 = CSATResponse(
        id="cs1", ticket_id="t1", rating=5, user_id="u1", submitted_at=now
    )
    c2 = CSATResponse(
        id="cs2", ticket_id="t2", rating=4, user_id="u2", submitted_at=now
    )
    c3 = CSATResponse(
        id="cs3", ticket_id="t3", rating=2, user_id="u3", submitted_at=now
    )
    db_session.add_all([c1, c2, c3])
    db_session.commit()

    csat = compute_csat_metrics(db_session, ["t1", "t2", "t3"])
    assert csat.total_responses == 3
    assert csat.average_rating == round((5 + 4 + 2) / 3, 2)  # 3.67
    assert csat.satisfied_count == 2  # 5 and 4
    assert csat.satisfaction_percentage == round((2 / 3) * 100, 2)  # 66.67
    assert csat.rating_distribution == {1: 0, 2: 1, 3: 0, 4: 1, 5: 1}


def test_generate_performance_report_full_flow(db_session: Session):
    """Verify performance report generation with filters and severity breakdowns."""
    now = datetime.now(timezone.utc)

    t1 = Ticket(
        id="t1",
        title="P1 Resolved",
        description="D1",
        severity=TicketSeverity.P1,
        status=TicketStatus.RESOLVED,
        organization_id="org_alpha",
        creator_user_id="u1",
        response_due_at=now,
        resolution_due_at=now,
        is_sla_breached=False,
        created_at=now - timedelta(hours=2),
        resolved_at=now,
    )
    t2 = Ticket(
        id="t2",
        title="P2 Breached",
        description="D2",
        severity=TicketSeverity.P2,
        status=TicketStatus.ESCALATED,
        organization_id="org_alpha",
        creator_user_id="u2",
        response_due_at=now,
        resolution_due_at=now,
        is_sla_breached=True,
        created_at=now - timedelta(hours=5),
    )
    t3 = Ticket(
        id="t3",
        title="P3 Other Org",
        description="D3",
        severity=TicketSeverity.P3,
        status=TicketStatus.RESOLVED,
        organization_id="org_beta",
        creator_user_id="u3",
        response_due_at=now,
        resolution_due_at=now,
        is_sla_breached=False,
        created_at=now - timedelta(hours=1),
        resolved_at=now,
    )
    db_session.add_all([t1, t2, t3])

    csat1 = CSATResponse(
        id="cs1", ticket_id="t1", rating=5, user_id="u1", submitted_at=now
    )
    db_session.add(csat1)
    db_session.commit()

    # Query for org_alpha
    report_alpha = generate_performance_report(
        db_session,
        organization_id="org_alpha",
    )

    assert report_alpha.total_tickets == 2
    assert report_alpha.resolved_tickets == 1
    assert report_alpha.open_tickets == 1
    assert report_alpha.sla_compliance.compliance_percentage == 50.0  # 1/2
    assert report_alpha.mttr.average_hours == 2.0
    assert report_alpha.csat.total_responses == 1
    assert report_alpha.csat.average_rating == 5.0

    # Severity breakdown check
    p1_breakdown = report_alpha.severity_breakdown["P1"]
    assert p1_breakdown["total_tickets"] == 1
    assert p1_breakdown["sla_compliance"]["compliance_percentage"] == 100.0

    p2_breakdown = report_alpha.severity_breakdown["P2"]
    assert p2_breakdown["total_tickets"] == 1
    assert p2_breakdown["sla_compliance"]["compliance_percentage"] == 0.0

    # Query all orgs
    report_all = generate_performance_report(db_session, organization_id="*")
    assert report_all.total_tickets == 3

    # Test dictionary export
    dict_rep = report_alpha.to_dict()
    assert dict_rep["total_tickets"] == 2
    assert dict_rep["mttr"]["average_hours"] == 2.0
    assert dict_rep["csat"]["average_rating"] == 5.0
