"""Unit tests for periodic Celery background worker task in escalation_worker.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.deal import (
    CriticalTask,
    Deal,
    DealStage,
    Milestone,
    MilestoneStatus,
)
from src.workers.celery_app import celery_app
from src.workers.escalation_worker import (
    _parse_reference_time,
    evaluate_overdue_tasks,
)


@pytest.fixture
def test_db_session():
    """Fixture providing an in-memory SQLite database session for escalation worker task tests."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_parse_reference_time_valid():
    """Test _parse_reference_time with valid inputs."""
    assert _parse_reference_time(None) is None

    now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    parsed = _parse_reference_time(now)
    assert parsed == now

    iso_str = "2026-03-15T12:00:00Z"
    parsed_iso = _parse_reference_time(iso_str)
    assert parsed_iso == now

    iso_str_tz = "2026-03-15T12:00:00+00:00"
    parsed_tz = _parse_reference_time(iso_str_tz)
    assert parsed_tz == now

    std_str = "2026-03-15 12:00:00"
    parsed_std = _parse_reference_time(std_str)
    assert parsed_std == now


def test_parse_reference_time_invalid():
    """Test _parse_reference_time raises ValueError on malformed string."""
    with pytest.raises(ValueError, match="Invalid datetime string format"):
        _parse_reference_time("not-a-datetime")


def test_evaluate_overdue_tasks_invalid_time_string(test_db_session: Session):
    """Test evaluate_overdue_tasks with invalid reference time string returns failure status."""
    result = evaluate_overdue_tasks(
        reference_time_str="invalid-date",
        correlation_id="test-cid-invalid",
        db_session=test_db_session,
    )
    assert result["status"] == "failed"
    assert "Invalid datetime string format" in result["error"]
    assert result["correlation_id"] == "test-cid-invalid"


def test_evaluate_overdue_tasks_empty_db(test_db_session: Session):
    """Test evaluate_overdue_tasks on empty database returns completed with zero counts."""
    result = evaluate_overdue_tasks(
        correlation_id="test-cid-empty",
        db_session=test_db_session,
    )
    assert result["status"] == "completed"
    assert result["correlation_id"] == "test-cid-empty"
    assert result["scanned_tasks_count"] == 0
    assert result["overdue_tasks_count"] == 0
    assert result["newly_escalated_count"] == 0
    assert result["updated_milestones_count"] == 0
    assert result["escalations"] == []


def test_evaluate_overdue_tasks_escalates_tasks(test_db_session: Session):
    """Test evaluate_overdue_tasks detects overdue critical tasks and escalates them."""
    ref_time = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    due_time = ref_time - timedelta(hours=30)  # 30 hours overdue

    stage = DealStage(id="stage-1", name="Negotiation", display_order=1)
    deal = Deal(
        id="deal-101",
        title="Enterprise Deal",
        stage_id="stage-1",
        assigned_team_member_id="supervisor-42",
        win_probability=0.7,
    )
    milestone = Milestone(
        id="ms-1",
        deal_id="deal-101",
        title="Contract Approval",
        target_date=ref_time + timedelta(days=5),
        status=MilestoneStatus.IN_PROGRESS,
    )
    task = CriticalTask(
        id="task-1",
        milestone_id="ms-1",
        title="Legal Review",
        due_date=due_time,
        is_critical=True,
        is_overdue=False,
        escalated_to_supervisor=False,
    )

    test_db_session.add_all([stage, deal, milestone, task])
    test_db_session.commit()

    result = evaluate_overdue_tasks(
        threshold_hours=24,
        reference_time_str=ref_time.isoformat(),
        correlation_id="test-cid-escalate",
        db_session=test_db_session,
    )

    assert result["status"] == "completed"
    assert result["correlation_id"] == "test-cid-escalate"
    assert result["scanned_tasks_count"] == 1
    assert result["overdue_tasks_count"] == 1
    assert result["newly_escalated_count"] == 1
    assert len(result["escalations"]) == 1

    escalation = result["escalations"][0]
    assert escalation["task_id"] == "task-1"
    assert escalation["deal_id"] == "deal-101"
    assert escalation["assigned_supervisor_id"] == "supervisor-42"

    # Verify task database state
    updated_task = test_db_session.query(CriticalTask).filter_by(id="task-1").first()
    assert updated_task is not None
    assert updated_task.is_overdue is True
    assert updated_task.escalated_to_supervisor is True

    # Verify milestone status updated to DELAYED
    updated_ms = test_db_session.query(Milestone).filter_by(id="ms-1").first()
    assert updated_ms is not None
    assert updated_ms.status == MilestoneStatus.DELAYED


def test_evaluate_overdue_tasks_handles_exception(test_db_session: Session):
    """Test evaluate_overdue_tasks catches exceptions during execution and returns failure dict."""
    with patch(
        "src.workers.escalation_worker.scan_and_escalate_overdue_tasks",
        side_effect=RuntimeError("Database connection lost"),
    ):
        result = evaluate_overdue_tasks(
            correlation_id="test-cid-error",
            db_session=test_db_session,
        )
        assert result["status"] == "failed"
        assert result["error"] == "Database connection lost"
        assert result["correlation_id"] == "test-cid-error"
