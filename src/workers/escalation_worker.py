"""Celery background worker task for periodic overdue critical task evaluation and supervisor escalations."""

from datetime import datetime, timezone
import logging
import uuid
from typing import Any, Dict, Optional, Union

from sqlalchemy.orm import Session

from src.lib.logging import clear_trace_id, set_trace_id
from src.models.base import SessionLocal
from src.services.deals.escalation_service import (
    evaluate_milestone_statuses,
    normalize_datetime,
    scan_and_escalate_overdue_tasks,
)
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _parse_reference_time(reference_time_str: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Parse reference time string or datetime object into UTC timezone-aware datetime.

    Args:
        reference_time_str: Optional datetime instance or ISO formatted timestamp string.

    Returns:
        Normalized UTC timezone-aware datetime instance, or None if input is None.

    Raises:
        ValueError: If string format cannot be parsed.
    """
    if reference_time_str is None:
        return None
    if isinstance(reference_time_str, datetime):
        return normalize_datetime(reference_time_str)
    if isinstance(reference_time_str, str) and reference_time_str.strip():
        s = reference_time_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return normalize_datetime(datetime.fromisoformat(s))
        except ValueError:
            try:
                return normalize_datetime(datetime.strptime(s, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                raise ValueError(
                    f"Invalid datetime string format: '{reference_time_str}'. Expected ISO 8601 format."
                )
    return None


@celery_app.task(name="src.workers.escalation_worker.evaluate_overdue_tasks")
def evaluate_overdue_tasks(
    threshold_hours: Optional[int] = None,
    reference_time_str: Optional[Union[str, datetime]] = None,
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Background Celery task to scan for overdue critical tasks and perform supervisor escalations.

    Scans critical tasks across active deal milestones, marks overdue tasks, triggers supervisor
    escalation alerts when overdue past configured thresholds, and updates parent milestone statuses (FR-3, FR-8).
    Emits structured JSON logs containing context trace IDs for operational auditability.

    Args:
        threshold_hours: Optional override for overdue threshold in hours.
        reference_time_str: Optional reference timestamp (datetime object or ISO string) for evaluation.
        correlation_id: Optional correlation/trace ID string for request context tracking.
        db_session: Optional pre-existing synchronous SQLAlchemy session (used for testing or direct invocation).

    Returns:
        Summary dictionary containing execution status, correlation_id, scanned_tasks_count,
        overdue_tasks_count, newly_escalated_count, updated_milestones_count, and escalations list.
    """
    task_cid = correlation_id or str(uuid.uuid4())
    set_trace_id(task_cid)

    try:
        ref_time = _parse_reference_time(reference_time_str)
    except ValueError as val_err:
        logger.error(
            "Invalid datetime string format passed to evaluate_overdue_tasks: %s",
            reference_time_str,
            extra={"correlation_id": task_cid},
        )
        clear_trace_id()
        return {
            "status": "failed",
            "error": str(val_err),
            "correlation_id": task_cid,
        }

    ref_time_iso = ref_time.isoformat() if ref_time else datetime.now(timezone.utc).isoformat()

    logger.info(
        "Starting periodic overdue task evaluation background task",
        extra={
            "correlation_id": task_cid,
            "threshold_hours": threshold_hours,
            "reference_time": ref_time_iso,
        },
    )

    owns_session = False
    session = db_session
    if session is None:
        session = SessionLocal()
        owns_session = True

    try:
        scan_result = scan_and_escalate_overdue_tasks(
            session=session,
            threshold_hours=threshold_hours,
            reference_time=ref_time,
            auto_commit=True,
        )

        updated_milestones_count = evaluate_milestone_statuses(
            session=session,
            reference_time=ref_time,
            auto_commit=True,
        )

        summary = {
            "status": "completed",
            "correlation_id": task_cid,
            "scanned_tasks_count": scan_result.scanned_tasks_count,
            "overdue_tasks_count": scan_result.overdue_tasks_count,
            "newly_escalated_count": scan_result.newly_escalated_count,
            "updated_milestones_count": updated_milestones_count,
            "escalations": [esc.to_dict() for esc in scan_result.escalations],
            "reference_time": ref_time_iso,
        }

        logger.info(
            "Completed overdue task evaluation task: scanned %d tasks, detected %d overdue, newly escalated %d tasks, updated %d milestones",
            summary["scanned_tasks_count"],
            summary["overdue_tasks_count"],
            summary["newly_escalated_count"],
            summary["updated_milestones_count"],
            extra={"correlation_id": task_cid, "summary": summary},
        )
        return summary
    except Exception as exc:
        logger.exception(
            "Error executing overdue task evaluation background task: %s",
            exc,
            extra={"correlation_id": task_cid},
        )
        if owns_session and session is not None:
            session.rollback()
        return {
            "status": "failed",
            "error": str(exc),
            "correlation_id": task_cid,
        }
    finally:
        if owns_session and session is not None:
            session.close()
        clear_trace_id()
