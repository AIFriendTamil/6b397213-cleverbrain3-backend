"""Service module for overdue task detection, supervisor escalation rules, and structured event logging."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import logging
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.lib.config import get_settings
from src.lib.logging import get_trace_id
from src.models.deal import CriticalTask, Deal, DealStage, Milestone, MilestoneStatus

logger = logging.getLogger(__name__)


@dataclass
class EscalationNotification:
    """Data payload representing an automated supervisor escalation notification."""

    task_id: str
    task_title: str
    milestone_id: str
    milestone_title: str
    deal_id: str
    deal_title: str
    assigned_supervisor_id: Optional[str]
    due_date: str
    hours_overdue: float
    escalated_at: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert notification instance to dictionary format."""
        return asdict(self)


@dataclass
class EscalationScanResult:
    """Summary metrics and notifications generated during an overdue task scan."""

    scanned_tasks_count: int
    overdue_tasks_count: int
    newly_escalated_count: int
    escalations: list[EscalationNotification]

    def to_dict(self) -> dict[str, Any]:
        """Convert scan result instance to dictionary format."""
        return {
            "scanned_tasks_count": self.scanned_tasks_count,
            "overdue_tasks_count": self.overdue_tasks_count,
            "newly_escalated_count": self.newly_escalated_count,
            "escalations": [esc.to_dict() for esc in self.escalations],
        }


def normalize_datetime(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware in UTC.

    Args:
        dt: Datetime object to normalize.

    Returns:
        Timezone-aware datetime in UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def calculate_overdue_hours(
    due_date: datetime,
    reference_time: Optional[datetime] = None,
) -> float:
    """Calculate the number of hours a task is overdue relative to reference_time.

    Args:
        due_date: Due date timestamp of the task.
        reference_time: Evaluation timestamp (defaults to current UTC time).

    Returns:
        Hours overdue as a non-negative float.
    """
    if reference_time is None:
        ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = normalize_datetime(reference_time)

    due_dt = normalize_datetime(due_date)
    delta_seconds = (ref_dt - due_dt).total_seconds()

    if delta_seconds <= 0:
        return 0.0

    return delta_seconds / 3600.0


def check_is_task_overdue(
    due_date: datetime,
    reference_time: Optional[datetime] = None,
) -> bool:
    """Check whether a task due date is strictly earlier than the reference time.

    Args:
        due_date: Due date timestamp of the task.
        reference_time: Evaluation timestamp (defaults to current UTC time).

    Returns:
        True if due_date is in the past relative to reference_time, False otherwise.
    """
    return calculate_overdue_hours(due_date, reference_time) > 0.0


def check_is_escalation_required(
    task: CriticalTask,
    threshold_hours: Optional[int] = None,
    reference_time: Optional[datetime] = None,
) -> bool:
    """Evaluate whether a critical task meets conditions for supervisor escalation.

    Args:
        task: CriticalTask ORM entity.
        threshold_hours: Configured overdue threshold in hours (defaults to config setting).
        reference_time: Evaluation timestamp (defaults to current UTC time).

    Returns:
        True if the task is critical, not yet escalated, and overdue beyond threshold_hours.
    """
    if not task.is_critical or task.escalated_to_supervisor:
        return False

    if threshold_hours is None:
        threshold_hours = get_settings().CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS

    hours_overdue = calculate_overdue_hours(task.due_date, reference_time)
    return hours_overdue >= float(threshold_hours)


def log_escalation_event(
    task_id: str,
    deal_id: str,
    supervisor_id: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Emit a structured JSON log event for a task supervisor escalation.

    Args:
        task_id: ID of the escalated task.
        deal_id: ID of the parent deal.
        supervisor_id: ID of the supervisor or assigned team member receiving alert.
        details: Additional context details dictionary.

    Returns:
        Structured log event payload dictionary.
    """
    event_id = str(uuid.uuid4())
    timestamp_str = datetime.now(timezone.utc).isoformat()
    trace_id = get_trace_id() or ""

    event_payload = {
        "event_id": event_id,
        "event_type": "task.supervisor_escalation",
        "timestamp": timestamp_str,
        "context_id": trace_id,
        "trace_id": trace_id,
        "deal_id": deal_id,
        "task_id": task_id,
        "supervisor_id": supervisor_id,
        "details": details or {},
    }

    logger.warning(
        "Critical task escalation triggered: task %s for deal %s escalated to supervisor %s",
        task_id,
        deal_id,
        supervisor_id,
        extra=event_payload,
    )

    return event_payload


def log_stage_transition_event(
    session: Session,
    deal_id: str,
    old_stage_id: Optional[str],
    new_stage_id: str,
    user_id: Optional[str] = None,
    task_id: Optional[str] = None,
    supervisor_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Emit a structured JSON log event for a pipeline deal stage transition.

    Args:
        session: SQLAlchemy DB session.
        deal_id: ID of the deal undergoing stage transition.
        old_stage_id: ID of the previous stage (or None for initial stage).
        new_stage_id: ID of the target stage.
        user_id: ID of the user performing transition.
        task_id: Optional task ID associated with transition.
        supervisor_id: Optional supervisor ID associated with transition.
        details: Additional context details dictionary.

    Returns:
        Structured log event payload dictionary.
    """
    event_id = str(uuid.uuid4())
    timestamp_str = datetime.now(timezone.utc).isoformat()
    trace_id = get_trace_id() or ""

    event_payload = {
        "event_id": event_id,
        "event_type": "pipeline.stage_transition",
        "timestamp": timestamp_str,
        "context_id": trace_id,
        "trace_id": trace_id,
        "deal_id": deal_id,
        "old_stage_id": old_stage_id or "",
        "new_stage_id": new_stage_id,
        "user_id": user_id or "",
        "task_id": task_id or "",
        "supervisor_id": supervisor_id or "",
        "details": details or {},
    }

    logger.info(
        "Pipeline stage transition recorded: deal %s moved from stage %s to stage %s",
        deal_id,
        old_stage_id,
        new_stage_id,
        extra=event_payload,
    )

    return event_payload


def transition_deal_stage(
    session: Session,
    deal_id: str,
    new_stage_id: str,
    user_id: Optional[str] = None,
    task_id: Optional[str] = None,
    supervisor_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    auto_commit: bool = True,
) -> tuple[Deal, dict[str, Any]]:
    """Transition a deal to a new stage, update timestamps, and emit a structured stage transition log event.

    Args:
        session: Synchronous SQLAlchemy DB session.
        deal_id: ID of the deal undergoing stage transition.
        new_stage_id: ID of the target deal stage.
        user_id: Optional user ID triggering the transition.
        task_id: Optional task ID associated with transition.
        supervisor_id: Optional supervisor ID associated with transition.
        details: Additional context details dictionary.
        auto_commit: Whether to commit changes to the database.

    Returns:
        Tuple of (updated Deal ORM entity, structured event payload dictionary).

    Raises:
        ValueError: If deal or target deal stage is not found in database.
    """
    deal = session.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise ValueError(f"Deal with id '{deal_id}' not found.")

    new_stage = session.query(DealStage).filter(DealStage.id == new_stage_id).first()
    if not new_stage:
        raise ValueError(f"Deal stage with id '{new_stage_id}' not found.")

    old_stage_id = deal.stage_id
    now = datetime.now(timezone.utc)

    deal.stage_id = new_stage_id
    deal.updated_at = now

    event_payload = log_stage_transition_event(
        session=session,
        deal_id=deal_id,
        old_stage_id=old_stage_id,
        new_stage_id=new_stage_id,
        user_id=user_id,
        task_id=task_id,
        supervisor_id=supervisor_id,
        details=details,
    )

    if auto_commit:
        session.commit()
    else:
        session.flush()

    return deal, event_payload


def escalate_critical_task(
    session: Session,
    task: CriticalTask,
    supervisor_id: Optional[str] = None,
    threshold_hours: Optional[int] = None,
    reference_time: Optional[datetime] = None,
) -> EscalationNotification:
    """Mark a critical task as overdue and escalated, update parent milestone, and create notification.

    Args:
        session: SQLAlchemy DB session.
        task: CriticalTask ORM entity.
        supervisor_id: Explicit supervisor ID override (if None, resolves from parent deal).
        threshold_hours: Overdue threshold hours used for context metadata.
        reference_time: Escalation timestamp (defaults to current UTC time).

    Returns:
        EscalationNotification instance.
    """
    if reference_time is None:
        escalation_dt = datetime.now(timezone.utc)
    else:
        escalation_dt = normalize_datetime(reference_time)

    hours_overdue = calculate_overdue_hours(task.due_date, escalation_dt)

    task.is_overdue = True
    task.escalated_to_supervisor = True
    task.escalated_at = escalation_dt
    task.updated_at = escalation_dt

    # Fetch parent milestone and update status to DELAYED if not completed
    milestone = task.milestone
    if not milestone and task.milestone_id:
        milestone = session.query(Milestone).filter(Milestone.id == task.milestone_id).first()

    milestone_id_val = ""
    milestone_title_val = ""
    deal_id_val = ""
    deal_title_val = ""
    resolved_supervisor_id = supervisor_id

    if milestone:
        milestone_id_val = milestone.id
        milestone_title_val = milestone.title
        if milestone.status != MilestoneStatus.COMPLETED:
            milestone.status = MilestoneStatus.DELAYED
            milestone.updated_at = escalation_dt

        deal = milestone.deal
        if not deal and milestone.deal_id:
            deal = session.query(Deal).filter(Deal.id == milestone.deal_id).first()

        if deal:
            deal_id_val = deal.id
            deal_title_val = deal.title
            if not resolved_supervisor_id:
                resolved_supervisor_id = deal.assigned_team_member_id

    if not resolved_supervisor_id:
        resolved_supervisor_id = "unassigned_supervisor"

    if threshold_hours is None:
        threshold_hours = get_settings().CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS

    notification = EscalationNotification(
        task_id=task.id,
        task_title=task.title,
        milestone_id=milestone_id_val,
        milestone_title=milestone_title_val,
        deal_id=deal_id_val,
        deal_title=deal_title_val,
        assigned_supervisor_id=resolved_supervisor_id,
        due_date=normalize_datetime(task.due_date).isoformat(),
        hours_overdue=round(hours_overdue, 2),
        escalated_at=escalation_dt.isoformat(),
        details={
            "is_critical": task.is_critical,
            "threshold_hours": threshold_hours,
            "milestone_status": milestone.status.value if milestone else None,
        },
    )

    log_escalation_event(
        task_id=task.id,
        deal_id=deal_id_val,
        supervisor_id=resolved_supervisor_id,
        details=notification.details,
    )

    session.flush()
    return notification


def scan_and_escalate_overdue_tasks(
    session: Session,
    threshold_hours: Optional[int] = None,
    reference_time: Optional[datetime] = None,
    auto_commit: bool = True,
) -> EscalationScanResult:
    """Scan all critical tasks across active milestones and escalate overdue tasks.

    Args:
        session: SQLAlchemy DB session.
        threshold_hours: Overdue threshold in hours (defaults to config setting).
        reference_time: Evaluation timestamp (defaults to current UTC time).
        auto_commit: Whether to commit session changes after scanning.

    Returns:
        EscalationScanResult summarizing the scan outcome.
    """
    if reference_time is None:
        ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = normalize_datetime(reference_time)

    if threshold_hours is None:
        threshold_hours = get_settings().CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS

    # Query critical tasks belonging to non-completed milestones
    query = (
        session.query(CriticalTask)
        .join(Milestone, CriticalTask.milestone_id == Milestone.id)
        .filter(
            CriticalTask.is_critical == True,  # noqa: E712
            Milestone.status != MilestoneStatus.COMPLETED,
        )
    )

    tasks = query.all()
    scanned_count = len(tasks)
    overdue_count = 0
    newly_escalated_count = 0
    escalations: list[EscalationNotification] = []

    for task in tasks:
        is_overdue = check_is_task_overdue(task.due_date, reference_time=ref_dt)
        if is_overdue:
            overdue_count += 1
            if not task.is_overdue:
                task.is_overdue = True
                task.updated_at = ref_dt

        if check_is_escalation_required(task, threshold_hours=threshold_hours, reference_time=ref_dt):
            notification = escalate_critical_task(
                session=session,
                task=task,
                threshold_hours=threshold_hours,
                reference_time=ref_dt,
            )
            escalations.append(notification)
            newly_escalated_count += 1

    if auto_commit and (newly_escalated_count > 0 or overdue_count > 0):
        session.commit()

    return EscalationScanResult(
        scanned_tasks_count=scanned_count,
        overdue_tasks_count=overdue_count,
        newly_escalated_count=newly_escalated_count,
        escalations=escalations,
    )


def evaluate_milestone_statuses(
    session: Session,
    deal_id: Optional[str] = None,
    reference_time: Optional[datetime] = None,
    auto_commit: bool = True,
) -> int:
    """Evaluate and update milestone status to DELAYED if past target_date or containing overdue tasks.

    Args:
        session: SQLAlchemy DB session.
        deal_id: Optional deal_id to restrict evaluation to a single deal.
        reference_time: Evaluation timestamp (defaults to current UTC time).
        auto_commit: Whether to commit changes after evaluation.

    Returns:
        Number of milestones updated to DELAYED.
    """
    if reference_time is None:
        ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = normalize_datetime(reference_time)

    query = session.query(Milestone).filter(Milestone.status != MilestoneStatus.COMPLETED)
    if deal_id:
        query = query.filter(Milestone.deal_id == deal_id)

    milestones = query.all()
    updated_count = 0

    for milestone in milestones:
        is_delayed = False

        # Target date passed
        if milestone.target_date and normalize_datetime(milestone.target_date) < ref_dt:
            is_delayed = True

        # Has overdue critical tasks
        if not is_delayed and milestone.critical_tasks:
            for task in milestone.critical_tasks:
                if task.is_overdue or check_is_task_overdue(task.due_date, reference_time=ref_dt):
                    is_delayed = True
                    break

        if is_delayed and milestone.status != MilestoneStatus.DELAYED:
            milestone.status = MilestoneStatus.DELAYED
            milestone.updated_at = ref_dt
            updated_count += 1

    if auto_commit and updated_count > 0:
        session.commit()

    return updated_count
