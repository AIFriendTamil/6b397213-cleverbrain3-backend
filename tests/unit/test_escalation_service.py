"""Unit tests for overdue task threshold detection and supervisor escalation in escalation_service.py."""

import io
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.lib.logging import JSONFormatter, clear_trace_id, set_trace_id
from src.models.base import Base, create_db_engine
from src.models.deal import (
    CriticalTask,
    Deal,
    DealStage,
    Milestone,
    MilestoneStatus,
)
from src.services.deals.escalation_service import (
    EscalationNotification,
    EscalationScanResult,
    calculate_overdue_hours,
    check_is_escalation_required,
    check_is_task_overdue,
    escalate_critical_task,
    evaluate_milestone_statuses,
    log_escalation_event,
    log_stage_transition_event,
    normalize_datetime,
    scan_and_escalate_overdue_tasks,
    transition_deal_stage,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_normalize_datetime():
    """Verify normalize_datetime converts naive datetimes to UTC timezone-aware."""
    naive_dt = datetime(2026, 1, 10, 12, 0, 0)
    aware_dt = normalize_datetime(naive_dt)
    assert aware_dt.tzinfo == timezone.utc
    assert aware_dt.year == 2026

    already_aware = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    res = normalize_datetime(already_aware)
    assert res == already_aware


def test_calculate_overdue_hours():
    """Verify calculation of overdue hours relative to reference timestamp."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    due_time = datetime(2026, 1, 9, 12, 0, 0, tzinfo=timezone.utc)

    hours = calculate_overdue_hours(due_time, reference_time=ref_time)
    assert hours == 24.0

    # Future due date should return 0.0
    future_due = datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert calculate_overdue_hours(future_due, reference_time=ref_time) == 0.0


def test_check_is_task_overdue():
    """Verify overdue detection boolean logic."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    past_due = datetime(2026, 1, 10, 11, 0, 0, tzinfo=timezone.utc)
    future_due = datetime(2026, 1, 10, 13, 0, 0, tzinfo=timezone.utc)

    assert check_is_task_overdue(past_due, reference_time=ref_time) is True
    assert check_is_task_overdue(future_due, reference_time=ref_time) is False


def test_check_is_escalation_required():
    """Verify escalation rule conditions based on criticality, status, and threshold."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    due_time = datetime(2026, 1, 9, 12, 0, 0, tzinfo=timezone.utc)  # 24 hours ago

    task_critical = CriticalTask(
        title="Review Contract",
        due_date=due_time,
        is_critical=True,
        escalated_to_supervisor=False,
    )

    # Overdue 24 hours with threshold 24 hours -> True
    assert check_is_escalation_required(task_critical, threshold_hours=24, reference_time=ref_time) is True

    # Higher threshold 48 hours -> False
    assert check_is_escalation_required(task_critical, threshold_hours=48, reference_time=ref_time) is False

    # Already escalated -> False
    task_critical.escalated_to_supervisor = True
    assert check_is_escalation_required(task_critical, threshold_hours=24, reference_time=ref_time) is False

    # Not critical -> False
    task_non_critical = CriticalTask(
        title="Optional Review",
        due_date=due_time,
        is_critical=False,
        escalated_to_supervisor=False,
    )
    assert check_is_escalation_required(task_non_critical, threshold_hours=24, reference_time=ref_time) is False


def test_escalate_critical_task(test_session: Session):
    """Verify escalating a critical task updates task flags, milestone status, and notification payload."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    due_time = datetime(2026, 1, 9, 12, 0, 0, tzinfo=timezone.utc)

    stage = DealStage(name="Negotiation", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Big Enterprise Deal",
        monetary_value=Decimal("500000.00"),
        stage_id=stage.id,
        assigned_team_member_id="sup_manager_99",
    )
    test_session.add(deal)
    test_session.commit()

    milestone = Milestone(
        deal_id=deal.id,
        title="Legal Approval",
        target_date=due_time,
        status=MilestoneStatus.IN_PROGRESS,
    )
    test_session.add(milestone)
    test_session.commit()

    task = CriticalTask(
        milestone_id=milestone.id,
        title="Sign NDA",
        due_date=due_time,
        is_critical=True,
        escalated_to_supervisor=False,
    )
    test_session.add(task)
    test_session.commit()

    notification = escalate_critical_task(
        session=test_session,
        task=task,
        threshold_hours=24,
        reference_time=ref_time,
    )

    assert task.is_overdue is True
    assert task.escalated_to_supervisor is True
    assert task.escalated_at == ref_time
    assert milestone.status == MilestoneStatus.DELAYED

    assert isinstance(notification, EscalationNotification)
    assert notification.task_id == task.id
    assert notification.task_title == "Sign NDA"
    assert notification.milestone_id == milestone.id
    assert notification.milestone_title == "Legal Approval"
    assert notification.deal_id == deal.id
    assert notification.deal_title == "Big Enterprise Deal"
    assert notification.assigned_supervisor_id == "sup_manager_99"
    assert notification.hours_overdue == 24.0

    notif_dict = notification.to_dict()
    assert notif_dict["task_id"] == task.id
    assert notif_dict["assigned_supervisor_id"] == "sup_manager_99"


def test_scan_and_escalate_overdue_tasks(test_session: Session):
    """Verify scanning database for overdue tasks and triggering escalations."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    due_overdue_28h = datetime(2026, 1, 9, 8, 0, 0, tzinfo=timezone.utc)  # 28 hours overdue
    due_overdue_10h = datetime(2026, 1, 10, 2, 0, 0, tzinfo=timezone.utc)  # 10 hours overdue
    due_future = datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc)

    stage = DealStage(name="Proposal", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Cloud Migration Deal",
        monetary_value=Decimal("200000.00"),
        stage_id=stage.id,
        assigned_team_member_id="usr_lead",
    )
    test_session.add(deal)
    test_session.commit()

    milestone = Milestone(
        deal_id=deal.id,
        title="Security Audit",
        target_date=due_overdue_28h,
        status=MilestoneStatus.PENDING,
    )
    test_session.add(milestone)
    test_session.commit()

    # Task 1: Overdue 28h (exceeds threshold 24h) -> Needs escalation
    task1 = CriticalTask(
        milestone_id=milestone.id,
        title="SOC2 Compliance Review",
        due_date=due_overdue_28h,
        is_critical=True,
        escalated_to_supervisor=False,
    )
    # Task 2: Overdue 10h (does not exceed threshold 24h) -> Overdue flag set, no escalation
    task2 = CriticalTask(
        milestone_id=milestone.id,
        title="Penetration Test Signoff",
        due_date=due_overdue_10h,
        is_critical=True,
        escalated_to_supervisor=False,
    )
    # Task 3: Future task -> No overdue, no escalation
    task3 = CriticalTask(
        milestone_id=milestone.id,
        title="Final Executive Briefing",
        due_date=due_future,
        is_critical=True,
        escalated_to_supervisor=False,
    )
    test_session.add_all([task1, task2, task3])
    test_session.commit()

    scan_result = scan_and_escalate_overdue_tasks(
        session=test_session,
        threshold_hours=24,
        reference_time=ref_time,
        auto_commit=True,
    )

    assert isinstance(scan_result, EscalationScanResult)
    assert scan_result.scanned_tasks_count == 3
    assert scan_result.overdue_tasks_count == 2
    assert scan_result.newly_escalated_count == 1
    assert len(scan_result.escalations) == 1
    assert scan_result.escalations[0].task_id == task1.id

    assert task1.escalated_to_supervisor is True
    assert task2.is_overdue is True
    assert task2.escalated_to_supervisor is False
    assert task3.is_overdue is False

    res_dict = scan_result.to_dict()
    assert res_dict["scanned_tasks_count"] == 3
    assert res_dict["newly_escalated_count"] == 1


def test_evaluate_milestone_statuses(test_session: Session):
    """Verify evaluating milestone statuses updates past target_date milestones to DELAYED."""
    ref_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    past_target = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
    future_target = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)

    stage = DealStage(name="Closing", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(title="SaaS Sub", stage_id=stage.id)
    test_session.add(deal)
    test_session.commit()

    m1 = Milestone(
        deal_id=deal.id,
        title="Contract Signoff",
        target_date=past_target,
        status=MilestoneStatus.PENDING,
    )
    m2 = Milestone(
        deal_id=deal.id,
        title="Onboarding Kickoff",
        target_date=future_target,
        status=MilestoneStatus.PENDING,
    )
    m3 = Milestone(
        deal_id=deal.id,
        title="Historical Milestone",
        target_date=past_target,
        status=MilestoneStatus.COMPLETED,  # Completed should not change to DELAYED
    )
    test_session.add_all([m1, m2, m3])
    test_session.commit()

    updated = evaluate_milestone_statuses(
        session=test_session,
        reference_time=ref_time,
        auto_commit=True,
    )

    assert updated == 1
    assert m1.status == MilestoneStatus.DELAYED
    assert m2.status == MilestoneStatus.PENDING
    assert m3.status == MilestoneStatus.COMPLETED


def test_log_escalation_event():
    """Verify log_escalation_event returns structured event payload dictionary."""
    payload = log_escalation_event(
        task_id="tsk_123",
        deal_id="dl_456",
        supervisor_id="sup_789",
        details={"reason": "24h overdue"},
    )

    assert payload["event_type"] == "task.supervisor_escalation"
    assert payload["task_id"] == "tsk_123"
    assert payload["deal_id"] == "dl_456"
    assert payload["supervisor_id"] == "sup_789"
    assert payload["details"]["reason"] == "24h overdue"
    assert "event_id" in payload
    assert "timestamp" in payload


def test_log_stage_transition_event(test_session: Session):
    """Verify log_stage_transition_event returns structured event payload dictionary."""
    payload = log_stage_transition_event(
        session=test_session,
        deal_id="dl_100",
        old_stage_id="stg_1",
        new_stage_id="stg_2",
        user_id="usr_admin",
        details={"notes": "Moved after negotiation"},
    )

    assert payload["event_type"] == "pipeline.stage_transition"
    assert payload["deal_id"] == "dl_100"
    assert payload["old_stage_id"] == "stg_1"
    assert payload["new_stage_id"] == "stg_2"
    assert payload["user_id"] == "usr_admin"
    assert payload["details"]["notes"] == "Moved after negotiation"


def test_structured_json_log_output_formatting():
    """Verify structured JSON log record emission with JSONFormatter for escalation and transition events."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setFormatter(JSONFormatter(default_component="deals_service"))

    target_logger = logging.getLogger("src.services.deals.escalation_service")
    target_logger.addHandler(handler)
    target_logger.setLevel(logging.INFO)

    set_trace_id("trc_9988776655")

    try:
        esc_payload = log_escalation_event(
            task_id="tsk_critical_01",
            deal_id="dl_enterprise_01",
            supervisor_id="sup_director_01",
            details={"priority": "P1", "hours_overdue": 36.5},
        )
        assert esc_payload["context_id"] == "trc_9988776655"

        log_lines = log_capture.getvalue().strip().split("\n")
        assert len(log_lines) >= 1

        json_record = json.loads(log_lines[-1])
        assert json_record["level"] == "WARNING"
        assert json_record["event_type"] == "task.supervisor_escalation"
        assert json_record["deal_id"] == "dl_enterprise_01"
        assert json_record["task_id"] == "tsk_critical_01"
        assert json_record["supervisor_id"] == "sup_director_01"
        assert json_record["trace_id"] == "trc_9988776655"
        assert json_record["details"]["priority"] == "P1"

    finally:
        clear_trace_id()
        target_logger.removeHandler(handler)


def test_transition_deal_stage(test_session: Session):
    """Verify transitioning a deal's stage updates ORM state and emits stage transition log event."""
    stg1 = DealStage(name="Discovery", display_order=1)
    stg2 = DealStage(name="Proposal", display_order=2)
    test_session.add_all([stg1, stg2])
    test_session.commit()

    deal = Deal(
        title="Fintech Partnership",
        monetary_value=Decimal("150000.00"),
        stage_id=stg1.id,
    )
    test_session.add(deal)
    test_session.commit()

    updated_deal, event_payload = transition_deal_stage(
        session=test_session,
        deal_id=deal.id,
        new_stage_id=stg2.id,
        user_id="usr_sales_rep_01",
        details={"reason": "Completed discovery call"},
    )

    assert updated_deal.stage_id == stg2.id
    assert event_payload["event_type"] == "pipeline.stage_transition"
    assert event_payload["deal_id"] == deal.id
    assert event_payload["old_stage_id"] == stg1.id
    assert event_payload["new_stage_id"] == stg2.id
    assert event_payload["user_id"] == "usr_sales_rep_01"
    assert event_payload["details"]["reason"] == "Completed discovery call"


def test_transition_deal_stage_errors(test_session: Session):
    """Verify transition_deal_stage raises ValueError when deal or target stage is missing."""
    stg = DealStage(name="Qualified", display_order=1)
    test_session.add(stg)
    test_session.commit()

    deal = Deal(title="Missing Stage Deal", stage_id=stg.id)
    test_session.add(deal)
    test_session.commit()

    with pytest.raises(ValueError, match="Deal with id 'missing_deal_id' not found"):
        transition_deal_stage(
            session=test_session,
            deal_id="missing_deal_id",
            new_stage_id=stg.id,
        )

    with pytest.raises(ValueError, match="Deal stage with id 'missing_stage_id' not found"):
        transition_deal_stage(
            session=test_session,
            deal_id=deal.id,
            new_stage_id="missing_stage_id",
        )
