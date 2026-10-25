"""Unit tests for SLA calculation engine, breach evaluation, and dynamic recalibration."""

from datetime import datetime, timedelta, timezone
import pytest

from src.lib.config import settings
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus
from src.services.escalation.sla_engine import (
    SLACountdownInfo,
    calculate_sla_due_dates,
    calculate_target_deadlines,
    evaluate_escalation_path,
    evaluate_sla_status,
    evaluate_ticket_breach,
    get_next_escalation_tier,
    get_sla_threshold_settings,
    recalibrate_sla_deadlines,
    recalibrate_ticket_sla,
    should_escalate_ticket,
)


def test_calculate_sla_due_dates_across_severities():
    """Verify SLA target due date calculations for P1, P2, and P3 incidents."""
    now = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)

    # P1
    resp_p1, res_p1 = calculate_sla_due_dates(TicketSeverity.P1, created_at=now)
    assert resp_p1 == now + timedelta(hours=1)
    assert res_p1 == now + timedelta(hours=4)

    # P2
    resp_p2, res_p2 = calculate_sla_due_dates("P2", created_at=now)
    assert resp_p2 == now + timedelta(hours=4)
    assert res_p2 == now + timedelta(hours=24)

    # P3
    resp_p3, res_p3 = calculate_sla_due_dates("p3", created_at=now)
    assert resp_p3 == now + timedelta(hours=24)
    assert res_p3 == now + timedelta(hours=72)

    # Alias check
    resp_alias, res_alias = calculate_target_deadlines(TicketSeverity.P1, created_at=now)
    assert resp_alias == resp_p1
    assert res_alias == res_p1


def test_calculate_sla_due_dates_invalid_severity():
    """Verify ValueError raised when passing invalid severity string."""
    with pytest.raises(ValueError) as exc_info:
        calculate_sla_due_dates("INVALID_SEV")
    assert "Invalid ticket severity classification" in str(exc_info.value)


def test_get_sla_threshold_settings():
    """Verify retrieval of threshold settings for P1, P2, P3 severities."""
    s1 = get_sla_threshold_settings(TicketSeverity.P1)
    assert s1["response_hours"] == 1.0
    assert s1["resolution_hours"] == 4.0
    assert s1["warning_threshold_percent"] == 75.0

    s2 = get_sla_threshold_settings("P2")
    assert s2["response_hours"] == 4.0
    assert s2["resolution_hours"] == 24.0
    assert s2["warning_threshold_percent"] == 80.0


def test_evaluate_ticket_breach_scenarios():
    """Verify breach detection logic under response and resolution target conditions."""
    created_at = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    resp_due = created_at + timedelta(hours=1)
    res_due = created_at + timedelta(hours=4)

    ticket = Ticket(
        id="t-100",
        title="Sample Ticket",
        description="Sample Desc",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        organization_id="org_1",
        creator_user_id="usr_1",
        created_at=created_at,
        response_due_at=resp_due,
        resolution_due_at=res_due,
        is_sla_breached=False,
    )

    # Evaluation before response due: Not breached
    t_early = created_at + timedelta(minutes=30)
    assert evaluate_ticket_breach(ticket, current_time=t_early) is False

    # Evaluation past response due without response: Breached
    t_past_resp = created_at + timedelta(hours=2)
    assert evaluate_ticket_breach(ticket, current_time=t_past_resp) is True

    # Assign agent/change status to IN_PROGRESS: Response target met, evaluation before resolution due -> Not breached
    ticket.status = TicketStatus.IN_PROGRESS
    ticket.assigned_agent_id = "agent_1"
    assert evaluate_ticket_breach(ticket, current_time=t_past_resp) is False

    # Evaluation past resolution due: Breached
    t_past_res = created_at + timedelta(hours=5)
    assert evaluate_ticket_breach(ticket, current_time=t_past_res) is True

    # Ticket resolved before resolution due: Not breached
    ticket.status = TicketStatus.RESOLVED
    ticket.resolved_at = created_at + timedelta(hours=3)
    assert evaluate_ticket_breach(ticket, current_time=t_past_res) is False


def test_evaluate_sla_status_countdown_and_warnings():
    """Verify SLACountdownInfo generation, percentage math, and warning indicators."""
    created_at = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    resp_due = created_at + timedelta(hours=1)  # 60 min
    res_due = created_at + timedelta(hours=4)   # 240 min

    ticket = Ticket(
        id="t-200",
        title="Critical Database Outage",
        description="DB unresponsive",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        organization_id="org_2",
        creator_user_id="usr_2",
        created_at=created_at,
        response_due_at=resp_due,
        resolution_due_at=res_due,
        is_sla_breached=False,
    )

    # At 50 minutes (83.3% of 60 min response window -> crosses 75% warning threshold)
    eval_time = created_at + timedelta(minutes=50)
    info = evaluate_sla_status(ticket, current_time=eval_time)

    assert isinstance(info, SLACountdownInfo)
    assert info.ticket_id == "t-200"
    assert info.severity == "P1"
    assert info.response_percent_elapsed == 83.33
    assert info.is_response_warning is True
    assert info.is_warning is True
    assert info.is_breached is False
    assert info.response_time_remaining_seconds == 600.0  # 10 minutes left

    # Dict conversion check
    d = info.to_dict()
    assert d["ticket_id"] == "t-200"
    assert d["is_warning"] is True


def test_recalibrate_ticket_sla_mid_lifecycle():
    """Verify EC-01: Dynamically recalibrating SLA deadlines from created_at upon severity update."""
    created_at = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)

    # Initially P3 (24h response, 72h resolution)
    ticket = Ticket(
        id="t-300",
        title="Minor Glitch Upgraded",
        description="Upgraded from P3 to P1",
        severity=TicketSeverity.P3,
        status=TicketStatus.NEW,
        organization_id="org_3",
        creator_user_id="usr_3",
        created_at=created_at,
        response_due_at=created_at + timedelta(hours=24),
        resolution_due_at=created_at + timedelta(hours=72),
        is_sla_breached=False,
    )

    # 2 hours after creation, upgrade from P3 to P1
    eval_time = created_at + timedelta(hours=2)
    updated_ticket = recalibrate_ticket_sla(ticket, TicketSeverity.P1, current_time=eval_time)

    assert updated_ticket.severity == TicketSeverity.P1
    # P1: 1h response, 4h resolution from created_at (8:00 -> 9:00 response_due, 12:00 resolution_due)
    assert updated_ticket.response_due_at == created_at + timedelta(hours=1)
    assert updated_ticket.resolution_due_at == created_at + timedelta(hours=4)

    # Since eval_time (10:00) > response_due_at (9:00) and ticket is NEW (unresponded), it is now breached!
    assert updated_ticket.is_sla_breached is True


def test_get_next_escalation_tier():
    """Verify escalation tier progression rules (Tier 1 -> Tier 2 -> Tier 3 -> None)."""
    assert get_next_escalation_tier(EscalationTier.TIER_1) == 2
    assert get_next_escalation_tier(1) == 2

    assert get_next_escalation_tier(EscalationTier.TIER_2) == 3
    assert get_next_escalation_tier(2) == 3

    assert get_next_escalation_tier(EscalationTier.TIER_3) is None
    assert get_next_escalation_tier(3) is None


def test_evaluate_escalation_path_normal_and_top_tier_fallback():
    """Verify FR-06 & EC-04: escalation decisions and top tier emergency fallback triggers."""
    created_at = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
    resp_due = created_at + timedelta(hours=1)
    res_due = created_at + timedelta(hours=4)

    ticket = Ticket(
        id="t-400",
        title="Persistent Breach",
        description="Breached ticket at Tier 1",
        severity=TicketSeverity.P1,
        status=TicketStatus.NEW,
        escalation_tier=EscalationTier.TIER_1,
        organization_id="org_4",
        creator_user_id="usr_4",
        created_at=created_at,
        response_due_at=resp_due,
        resolution_due_at=res_due,
        is_sla_breached=True,
    )

    eval_time = created_at + timedelta(hours=2)

    # Tier 1 breached -> should escalate to Tier 2
    esc1 = evaluate_escalation_path(ticket, current_time=eval_time)
    assert esc1["should_escalate"] is True
    assert esc1["current_tier"] == 1
    assert esc1["next_tier"] == 2
    assert esc1["is_top_tier_reached"] is False
    assert esc1["requires_emergency_fallback"] is False

    # Tier 3 breached -> top tier reached, emergency fallback required (EC-04)
    ticket.escalation_tier = EscalationTier.TIER_3
    esc3 = evaluate_escalation_path(ticket, current_time=eval_time)
    assert esc3["should_escalate"] is True
    assert esc3["current_tier"] == 3
    assert esc3["next_tier"] is None
    assert esc3["is_top_tier_reached"] is True
    assert esc3["requires_emergency_fallback"] is True
    assert "Emergency fallback notification required" in esc3["reason"]


def test_should_escalate_ticket_resolved_not_escalated():
    """Verify resolved or non-breached tickets do not trigger escalation."""
    created_at = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
    ticket = Ticket(
        id="t-500",
        title="Resolved Ticket",
        description="Resolved on time",
        severity=TicketSeverity.P2,
        status=TicketStatus.RESOLVED,
        escalation_tier=EscalationTier.TIER_1,
        organization_id="org_5",
        creator_user_id="usr_5",
        created_at=created_at,
        response_due_at=created_at + timedelta(hours=4),
        resolution_due_at=created_at + timedelta(hours=24),
        resolved_at=created_at + timedelta(hours=2),
        is_sla_breached=False,
    )

    assert should_escalate_ticket(ticket, current_time=created_at + timedelta(hours=10)) is False
