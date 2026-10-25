"""Celery background worker tasks for multi-channel notification dispatches and exponential backoff retry handling."""

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from src.connectors.email_adapter import (
    EmailAdapter,
    EmailAdapterError,
    EmailGatewayDeliveryError,
    EmailGatewayUnavailableError,
)
from src.connectors.sms_adapter import (
    SMSAdapter,
    SMSAdapterError,
    SMSGatewayDeliveryError,
    SMSGatewayUnavailableError,
)
from src.lib.logging import clear_trace_id, set_trace_id
from src.models.base import SessionLocal
from src.models.notification import NotificationCategory
from src.services.notifications.dispatcher import (
    DispatchStatus,
    DispatchSummary,
    DispatcherValidationError,
    dispatch_notification,
    redact_pii_for_logging,
)
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=2,
    name="src.workers.notification_tasks.dispatch_notification_task",
)
def dispatch_notification_task(
    self: Any,
    user_id: str,
    category: Union[NotificationCategory, str],
    title: str,
    message: str,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    force_in_app: bool = False,
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
    email_adapter: Optional[EmailAdapter] = None,
    sms_adapter: Optional[SMSAdapter] = None,
    retry_on_gateway_failure: bool = True,
) -> Dict[str, Any]:
    """Background Celery task to evaluate user preferences and dispatch multi-channel notifications with exponential backoff retries.

    Evaluates user channel preferences prior to dispatching alerts via email, SMS, or in-app channels (FR-10).
    Stores real-time in-app alerts in the central notification hub (FR-8). Catches gateway disruptions and applies
    exponential backoff retries up to 3 times (NFR-5). Emits structured PII-redacted log entries omitting recipient PII.

    Args:
        self: Bound Celery task instance for retry control.
        user_id: Target user identifier UUID string.
        category: Notification category (Financial, Milestone, SLA).
        title: Notification summary header title string.
        message: Detailed notification message body string.
        email: Optional recipient email address string.
        phone_number: Optional recipient phone number in E.164 format string.
        force_in_app: If True, forces in-app notification creation regardless of preference setting.
        correlation_id: Optional correlation/trace ID string for log context tracking.
        db_session: Optional pre-existing synchronous SQLAlchemy session (used for testing or direct invocation).
        email_adapter: Optional pre-configured EmailAdapter instance.
        sms_adapter: Optional pre-configured SMSAdapter instance.
        retry_on_gateway_failure: If True, triggers task retry when gateway delivery fails with retryable error.

    Returns:
        Summary dictionary containing execution status, correlation ID, user ID, category,
        dispatch summary dictionary, and channel result details.
    """
    task_cid = correlation_id or str(uuid.uuid4())
    set_trace_id(task_cid)

    category_str = category.value if isinstance(category, NotificationCategory) else str(category)

    logger.info(
        "Starting background notification dispatch task for user %s (category: %s)",
        user_id,
        category_str,
        extra={
            "correlation_id": task_cid,
            "user_id": user_id,
            "category": category_str,
            "retry_count": self.request.retries if hasattr(self, "request") and self.request else 0,
        },
    )

    owns_session = False
    session = db_session
    if session is None:
        session = SessionLocal()
        owns_session = True

    try:
        active_email_adapter = email_adapter or EmailAdapter()
        active_sms_adapter = sms_adapter or SMSAdapter()

        gateway_exception: Optional[Exception] = None

        def custom_email_sender(recip: str, subj: str, body_text: str) -> bool:
            nonlocal gateway_exception
            try:
                return active_email_adapter.send_email(recip, subj, body_text)
            except (EmailGatewayUnavailableError, EmailGatewayDeliveryError, EmailAdapterError) as gateway_err:
                gateway_exception = gateway_err
                raise gateway_err

        def custom_sms_sender(recip_phone: str, body_text: str) -> bool:
            nonlocal gateway_exception
            try:
                return active_sms_adapter.send_sms(recip_phone, body_text)
            except (SMSGatewayUnavailableError, SMSGatewayDeliveryError, SMSAdapterError) as gateway_err:
                gateway_exception = gateway_err
                raise gateway_err

        summary: DispatchSummary = dispatch_notification(
            db=session,
            user_id=user_id,
            category=category,
            title=title,
            message=message,
            email=email,
            phone_number=phone_number,
            email_sender=custom_email_sender,
            sms_sender=custom_sms_sender,
            force_in_app=force_in_app,
        )

        has_gateway_failure = False
        for channel_name, channel_res in summary.results.items():
            if channel_res.status == DispatchStatus.FAILED:
                has_gateway_failure = True
                break

        current_retries = self.request.retries if hasattr(self, "request") and self.request else 0

        if (has_gateway_failure or gateway_exception) and retry_on_gateway_failure:
            if current_retries < self.max_retries:
                countdown = self.default_retry_delay * (2 ** current_retries)
                retry_exc = gateway_exception or Exception(
                    f"Notification dispatch failed on one or more channels for user {user_id}"
                )

                logger.warning(
                    "Notification dispatch encountered gateway failure on attempt %d/%d. Retrying in %ds...",
                    current_retries + 1,
                    self.max_retries + 1,
                    countdown,
                    extra={
                        "correlation_id": task_cid,
                        "user_id": user_id,
                        "category": category_str,
                        "retry_count": current_retries,
                        "countdown_seconds": countdown,
                    },
                )

                if owns_session and session:
                    session.commit()

                raise self.retry(exc=retry_exc, countdown=countdown)

        if owns_session and session:
            session.commit()

        result_payload = {
            "status": "completed",
            "correlation_id": task_cid,
            "user_id": user_id,
            "category": category_str,
            "summary": summary.to_log_dict(),
            "results": [res.to_dict() for res in summary.results.values()],
        }

        logger.info(
            "Successfully completed background notification dispatch for user %s",
            user_id,
            extra={
                "correlation_id": task_cid,
                "dispatch_result": redact_pii_for_logging(result_payload),
            },
        )

        return result_payload

    except DispatcherValidationError as val_err:
        logger.error(
            "Validation error in notification dispatch task for user %s: %s",
            user_id,
            str(val_err),
            extra={"correlation_id": task_cid, "user_id": user_id},
        )
        if owns_session and session:
            session.rollback()
        return {
            "status": "failed",
            "error": f"Validation error: {str(val_err)}",
            "correlation_id": task_cid,
            "user_id": user_id,
            "category": category_str,
        }

    except Exception as exc:
        current_retries = self.request.retries if hasattr(self, "request") and self.request else 0
        if exc.__class__.__name__ == "Retry":
            raise exc

        if retry_on_gateway_failure and current_retries < self.max_retries:
            countdown = self.default_retry_delay * (2 ** current_retries)
            logger.warning(
                "Unhandled error in notification dispatch task on attempt %d/%d. Retrying in %ds: %s",
                current_retries + 1,
                self.max_retries + 1,
                countdown,
                str(exc),
                extra={"correlation_id": task_cid, "user_id": user_id},
            )
            if owns_session and session:
                session.rollback()
            raise self.retry(exc=exc, countdown=countdown)

        logger.error(
            "Failed notification dispatch task for user %s after max retries: %s",
            user_id,
            str(exc),
            extra={"correlation_id": task_cid, "user_id": user_id, "error": str(exc)},
        )
        if owns_session and session:
            session.rollback()

        return {
            "status": "failed",
            "error": str(exc),
            "correlation_id": task_cid,
            "user_id": user_id,
            "category": category_str,
        }

    finally:
        if owns_session and session:
            session.close()
        clear_trace_id()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=2,
    name="src.workers.notification_tasks.send_notification_task",
)
def send_notification_task(
    self: Any,
    user_id: str,
    category: Union[NotificationCategory, str],
    title: str,
    message: str,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    force_in_app: bool = False,
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Alias Celery task endpoint for send_notification_task delegating to dispatch_notification_task."""
    return dispatch_notification_task(
        self=self,
        user_id=user_id,
        category=category,
        title=title,
        message=message,
        email=email,
        phone_number=phone_number,
        force_in_app=force_in_app,
        correlation_id=correlation_id,
        db_session=db_session,
    )


@celery_app.task(
    name="src.workers.notification_tasks.batch_dispatch_notifications_task"
)
def batch_dispatch_notifications_task(
    user_notifications: List[Dict[str, Any]],
    correlation_id: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Background Celery task to batch-dispatch notifications to multiple users.

    Args:
        user_notifications: List of dictionaries containing user notification parameters.
        correlation_id: Optional trace ID.
        db_session: Optional pre-existing synchronous SQLAlchemy session.

    Returns:
        Summary dictionary aggregating batch dispatch counts and item results.
    """
    task_cid = correlation_id or str(uuid.uuid4())
    set_trace_id(task_cid)

    logger.info(
        "Starting batch notification dispatch task for %d items",
        len(user_notifications),
        extra={"correlation_id": task_cid, "total_items": len(user_notifications)},
    )

    owns_session = False
    session = db_session
    if session is None:
        session = SessionLocal()
        owns_session = True

    completed_count = 0
    failed_count = 0
    item_results: List[Dict[str, Any]] = []

    try:
        for item in user_notifications:
            u_id = item.get("user_id")
            cat = item.get("category", "Financial")
            title = item.get("title", "Notification Alert")
            msg = item.get("message", "")
            email = item.get("email")
            phone = item.get("phone_number")
            force_in_app = item.get("force_in_app", False)

            try:
                res = dispatch_notification(
                    db=session,
                    user_id=u_id,
                    category=cat,
                    title=title,
                    message=msg,
                    email=email,
                    phone_number=phone,
                    force_in_app=force_in_app,
                )
                completed_count += 1
                item_results.append(
                    {
                        "user_id": u_id,
                        "status": "success",
                        "summary": res.to_log_dict(),
                    }
                )
            except Exception as err:
                failed_count += 1
                item_results.append(
                    {
                        "user_id": u_id,
                        "status": "failed",
                        "error": str(err),
                    }
                )

        if owns_session and session:
            session.commit()

        return {
            "status": "completed",
            "correlation_id": task_cid,
            "total_count": len(user_notifications),
            "completed_count": completed_count,
            "failed_count": failed_count,
            "items": item_results,
        }

    finally:
        if owns_session and session:
            session.close()
        clear_trace_id()
