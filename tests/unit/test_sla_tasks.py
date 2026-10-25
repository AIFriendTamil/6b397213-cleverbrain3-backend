"""Unit tests for periodic Celery background worker tasks in sla_tasks.py."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.audit_log import TicketEvent, TicketEventType
from src.models.base import Base, create_db_engine
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus
from src.workers.sla_tasks import (
    _parse_reference_time,
    check_sla_breaches,
    evaluate_sla_breaches,
)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for SLA worker task tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_parse_reference_time_valid_and_invalid():
    """Test _parse_reference_time with valid timestamp types and invalid strings."""
    assert _parse_reference_time(None) is None

    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert _parse_reference_time(now) == now

    naive = datetime(2026, 6, 1, 10, 0, 0)
    parsed_naive = _parse_reference_time(naive)
    assert parsed_naive.tzinfo == timezone.utc

    iso_str = "2026-06-01T10:00:00Z"
    assert _parse_reference_time(iso_str) == now

    iso_tz_str = "2026-06-01T10:00:00+00:00"
    assert _parse_reference_time(iso_tz_str) == now

    std_str = "2026-06-01 10:00:00"
    assert _parse_reference_time(std_str) == now

    with pytest.raises(ValueError, match="Invalid datetime string format"):
        _parse_reference_time("invalid-datetime-string")


def test_evaluate_sla_breaches_invalid_time_string(test_db_session: Session):
    """Test evaluate_sla_breaches returns failure status when given invalid datetime string."""
    result = evaluate_sla_breaches(
        reference_time_str="bad-timestamp",
        correlation_id="cid-test-bad-time",
        db_session=test_db_session,
    )
    assert result["status"] == "failed"
    assert "Invalid datetime string format" in result["error"]
    assert result["correlation_id"] == "cid-test-bad-time"


def test_evaluate_sla_breaches_empty_db(test_db_session: Session):
    """Test evaluate_sla_breaches on empty database returns completed status with zero counts."""
    result = evaluate_sla_breaches(
        correlation_id="cid-empty-db",
        db_session=test_db_session,
    )
    assert result["status"] == "completed"
    assert result["correlation_id"] == "cid-empty-db"
    assert result["evaluated_tickets_count"] == 0
    assert result["breached_tickets_count"] == 0
    assert result["newly_escalated_count"] == 0
    assert result["emergency_fallback_alerts_count"] == 0
    assert result["escalations"] == []
    assert result["fallback_alerts"] == []


def test_evaluate_sla_breaches_no_breached_tickets(test_db_session: Session):
    """Test evaluate_sla_breaches when active ticket is within SLA targets."""
    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    created_at = now - timedelta(minutes=10)  # P1 created 10 mins ago (due in 1h)

    ticket = Ticket(
        id="ticket-within-sla",
        title="Server slowdown",
        description="Performance degraded slightly",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        escalation_tier=EscalationTier.TIER_1,
        organization_id="org-acme",
        creator_user_id="user-1",
        response_due_at=created_at + timedelta(hours=1),
        resolution_due_at=created_at + timedelta(hours=4),
        is_sla_breached=False,
        created_at=created_at,
        updated_at=created_at,
    )
    test_db_session.add(ticket)
    test_db_session.commit()

    result = evaluate_sla_breaches(
        reference_time_str=now.isoformat(),
        correlation_id="cid-within-sla",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["evaluated_tickets_count"] == 1
    assert result["breached_tickets_count"] == 0
    assert result["newly_escalated_count"] == 0
    assert result["emergency_fallback_alerts_count"] == 0

    reloaded = test_db_session.query(Ticket).get("ticket-within-sla")
    assert reloaded.is_sla_breached is False
    assert reloaded.escalation_tier == EscalationTier.TIER_1
    assert reloaded.status == TicketStatus.NEW


def test_evaluate_sla_breaches_escalates_tier1_to_tier2(test_db_session: Session):
    """Test evaluate_sla_breaches detects SLA breach and advances ticket from Tier 1 to Tier 2 (FR-05, FR-06)."""
    ref_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    created_at = ref_time - timedelta(hours=2)  # P1 created 2 hours ago (response due in 1h)

    ticket = Ticket(
        id="ticket-breached-p1",
        title="Production Outage",
        description="Database node offline",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        escalation_tier=EscalationTier.TIER_1,
        organization_id="org-enterprise",
        creator_user_id="user-ops",
        response_due_at=created_at + timedelta(hours=1),
        resolution_due_at=created_at + timedelta(hours=4),
        is_sla_breached=False,
        created_at=created_at,
        updated_at=created_at,
    )
    test_db_session.add(ticket)
    test_db_session.commit()

    result = evaluate_sla_breaches(
        reference_time_str=ref_time,
        correlation_id="cid-escalate-t1-t2",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["evaluated_tickets_count"] == 1
    assert result["breached_tickets_count"] == 1
    assert result["newly_escalated_count"] == 1
    assert result["emergency_fallback_alerts_count"] == 0
    assert len(result["escalations"]) == 1

    esc_item = result["escalations"][0]
    assert esc_item["ticket_id"] == "ticket-breached-p1"
    assert esc_item["previous_tier"] == 1
    assert esc_item["new_tier"] == 2
    assert esc_item["previous_status"] == "NEW"
    assert esc_item["new_status"] == "ESCALATED"

    reloaded = test_db_session.query(Ticket).get("ticket-breached-p1")
    assert reloaded.is_sla_breached is True
    assert reloaded.escalation_tier == EscalationTier.TIER_2
    assert reloaded.status == TicketStatus.ESCALATED

    events = (
        test_db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == "ticket-breached-p1")
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == TicketEventType.ESCALATION_TRIGGERED
    assert "Escalation path advanced from Tier 1 to Tier 2" in events[0].content


def test_evaluate_sla_breaches_escalates_tier2_to_tier3(test_db_session: Session):
    """Test evaluate_sla_breaches advances breached ticket from Tier 2 to Tier 3 (FR-06)."""
    ref_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    created_at = ref_time - timedelta(hours=5)  # P1 created 5 hrs ago (resolution due in 4h)

    ticket = Ticket(
        id="ticket-tier2-breached",
        title="API Gateway Down",
        description="All external routes timing out",
        severity=TicketSeverity.P1,
        status=TicketStatus.ESCALATED,
        escalation_tier=EscalationTier.TIER_2,
        organization_id="org-fintech",
        creator_user_id="user-dev",
        response_due_at=created_at + timedelta(hours=1),
        resolution_due_at=created_at + timedelta(hours=4),
        is_sla_breached=True,
        created_at=created_at,
        updated_at=created_at,
    )
    test_db_session.add(ticket)
    test_db_session.commit()

    result = evaluate_sla_breaches(
        reference_time_str=ref_time,
        correlation_id="cid-escalate-t2-t3",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["breached_tickets_count"] == 1
    assert result["newly_escalated_count"] == 1
    assert len(result["escalations"]) == 1

    esc_item = result["escalations"][0]
    assert esc_item["previous_tier"] == 2
    assert esc_item["new_tier"] == 3

    reloaded = test_db_session.query(Ticket).get("ticket-tier2-breached")
    assert reloaded.escalation_tier == EscalationTier.TIER_3


def test_evaluate_sla_breaches_emergency_fallback_at_tier3(test_db_session: Session):
    """Test evaluate_sla_breaches triggers emergency fallback alert when top tier ticket breaches SLA (EC-04)."""
    ref_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    created_at = ref_time - timedelta(hours=10)  # P1 created 10 hrs ago (resolution due in 4h)

    ticket = Ticket(
        id="ticket-top-tier-breach",
        title="Core Database Corruption",
        description="Data store unreadable",
        severity=TicketSeverity.P1,
        status=TicketStatus.ESCALATED,
        escalation_tier=EscalationTier.TIER_3,
        organization_id="org-critical",
        creator_user_id="user-admin",
        response_due_at=created_at + timedelta(hours=1),
        resolution_due_at=created_at + timedelta(hours=4),
        is_sla_breached=True,
        created_at=created_at,
        updated_at=created_at,
    )
    test_db_session.add(ticket)
    test_db_session.commit()

    result = evaluate_sla_breaches(
        reference_time_str=ref_time,
        correlation_id="cid-emergency-fallback",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["breached_tickets_count"] == 1
    assert result["newly_escalated_count"] == 0
    assert result["emergency_fallback_alerts_count"] == 1
    assert len(result["fallback_alerts"]) == 1

    fb_item = result["fallback_alerts"][0]
    assert fb_item["ticket_id"] == "ticket-top-tier-breach"
    assert fb_item["organization_id"] == "org-critical"
    assert fb_item["escalation_tier"] == 3

    events = (
        test_db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == "ticket-top-tier-breach")
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == TicketEventType.ESCALATION_TRIGGERED
    assert "EMERGENCY FALLBACK ALERT" in events[0].content

    # Re-run task again to test idempotency / duplicate log suppression
    result2 = evaluate_sla_breaches(
        reference_time_str=ref_time,
        correlation_id="cid-emergency-fallback-run2",
        db_session=test_db_session,
    )
    assert result2["status"] == "completed"
    assert result2["emergency_fallback_alerts_count"] == 1

    events_after_run2 = (
        test_db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == "ticket-top-tier-breach")
        .all()
    )
    # Should still be 1 event (no duplicate event log added)
    assert len(events_after_run2) == 1


def test_evaluate_sla_breaches_organization_filter(test_db_session: Session):
    """Test evaluate_sla_breaches respects organization_id filtering."""
    ref_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    created_at = ref_time - timedelta(hours=2)

    t1 = Ticket(
        id="ticket-org-a",
        title="Issue Org A",
        description="Desc A",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        escalation_tier=EscalationTier.TIER_1,
        organization_id="org-A",
        creator_user_id="user-a",
        response_due_at=created_at + timedelta(hours=1),
        resolution_due_at=created_at + timedelta(hours=4),
        is_sla_breached=False,
        created_at=created_at,
        updated_at=created_at,
    )
    t2 = Ticket(
        id="ticket-org-b",
        title="Issue Org B",
        description="Desc B",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        escalation_tier=EscalationTier.TIER_1,
        organization_id="org-B",
        creator_user_id="user-b",
        response_due_at=created_at + timedelta(hours=1),
        resolution_due_at=created_at + timedelta(hours=4),
        is_sla_breached=False,
        created_at=created_at,
        updated_at=created_at,
    )
    test_db_session.add_all([t1, t2])
    test_db_session.commit()

    result = evaluate_sla_breaches(
        reference_time_str=ref_time,
        organization_id="org-A",
        correlation_id="cid-org-filter",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["evaluated_tickets_count"] == 1
    assert result["breached_tickets_count"] == 1
    assert result["escalations"][0]["ticket_id"] == "ticket-org-a"

    # Ticket B should remain un-evaluated / unchanged
    t2_reloaded = test_db_session.query(Ticket).get("ticket-org-b")
    assert t2_reloaded.is_sla_breached is False


def test_check_sla_breaches_alias(test_db_session: Session):
    """Test check_sla_breaches alias function delegates to evaluate_sla_breaches."""
    result = check_sla_breaches(
        correlation_id="cid-alias-check",
        db_session=test_db_session,
    )
    assert result["status"] == "completed"
    assert result["correlation_id"] == "cid-alias-check"
