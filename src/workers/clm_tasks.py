"""Celery background worker task for daily contract expiration evaluation and renewal warning dispatch."""

import logging
import uuid
from datetime import date as date_type, datetime, timezone
from typing import Any, Dict, Optional, Union

from sqlalchemy.orm import Session

from src.lib.logging import clear_trace_id, set_trace_id
from src.models.base import SessionLocal
from src.services.clm.renewal_service import process_contract_renewals
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="src.workers.clm_tasks.evaluate_contract_expirations")
def evaluate_contract_expirations(
    as_of_date_str: Optional[Union[str, date_type]] = None,
    force_redispatch: bool = False,
    exact_day_only: bool = False,
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Background Celery task to evaluate active contract expirations and dispatch renewal warnings.

    Enforces daily idempotent renewal warning dispatches at 90, 60, and 30 days prior to expiration
    (FR-3, FR-4, NFR-4). Emits structured JSON logs containing correlation IDs for context tracing (NFR-5).

    Args:
        as_of_date_str: Optional evaluation target date string ('YYYY-MM-DD') or date object.
        force_redispatch: If True, bypasses idempotency checks.
        exact_day_only: If True, evaluates only exact 90, 60, 30 day milestones instead of range windows.
        correlation_id: Optional correlation/trace ID string for request context tracking.
        db_session: Optional pre-existing synchronous SQLAlchemy session (used for testing or direct invocation).

    Returns:
        Summary dictionary containing status, correlation_id, evaluated_contracts_count,
        dispatched_count, skipped_count, dispatched_logs, and as_of_date.
    """
    task_cid = correlation_id or str(uuid.uuid4())
    set_trace_id(task_cid)

    # Resolve evaluation target date
    target_date: Optional[date_type] = None
    if as_of_date_str is not None:
        if isinstance(as_of_date_str, date_type):
            target_date = as_of_date_str
        elif isinstance(as_of_date_str, str) and as_of_date_str.strip():
            try:
                target_date = datetime.strptime(as_of_date_str.strip(), "%Y-%m-%d").date()
            except ValueError as val_err:
                logger.error(
                    "Invalid date format passed to evaluate_contract_expirations: %s",
                    as_of_date_str,
                    extra={"correlation_id": task_cid},
                )
                clear_trace_id()
                return {
                    "status": "failed",
                    "error": f"Invalid date string format: '{as_of_date_str}'. Expected 'YYYY-MM-DD'.",
                    "correlation_id": task_cid,
                }

    target_date_str = target_date.isoformat() if target_date else datetime.now(timezone.utc).date().isoformat()

    logger.info(
        "Starting daily contract expiration evaluation background task",
        extra={
            "correlation_id": task_cid,
            "as_of_date": target_date_str,
            "force_redispatch": force_redispatch,
            "exact_day_only": exact_day_only,
        },
    )

    owns_session = False
    session = db_session
    if session is None:
        session = SessionLocal()
        owns_session = True

    try:
        result = process_contract_renewals(
            db_session=session,
            as_of_date=target_date,
            force_redispatch=force_redispatch,
            exact_day_only=exact_day_only,
            roles=["worker"],
        )

        result["status"] = "completed"
        result["correlation_id"] = task_cid

        logger.info(
            "Completed contract expiration evaluation task: evaluated %d active contracts, dispatched %d renewal warnings, skipped %d",
            result.get("evaluated_contracts_count", 0),
            result.get("dispatched_count", 0),
            result.get("skipped_count", 0),
            extra={
                "correlation_id": task_cid,
                "as_of_date": target_date_str,
                "evaluated_contracts_count": result.get("evaluated_contracts_count"),
                "dispatched_count": result.get("dispatched_count"),
                "skipped_count": result.get("skipped_count"),
            },
        )
        return result

    except Exception as exc:
        logger.error(
            "Error evaluating contract expirations in background task: %s",
            exc,
            exc_info=True,
            extra={"correlation_id": task_cid, "as_of_date": target_date_str},
        )
        if session and session.is_active:
            session.rollback()
        return {
            "status": "failed",
            "error": str(exc),
            "correlation_id": task_cid,
            "as_of_date": target_date_str,
        }

    finally:
        if owns_session and session:
            session.close()
        clear_trace_id()
