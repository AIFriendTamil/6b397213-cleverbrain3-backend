"""Domain service for notification preference evaluation, channel delivery routing, and PII-redacted logging."""

import enum
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from src.models.notification import NotificationCategory
from src.services.notifications.hub import create_notification
from src.services.notifications.preference import (
    PreferenceValidationError,
    get_user_preference_by_category,
    is_valid_e164_phone,
    parse_notification_category,
)

logger = logging.getLogger(__name__)

# Patterns for scrubbing PII from raw log messages
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
E164_PHONE_SEARCH_REGEX = re.compile(r"\+[1-9]\d{1,14}")

# Keys that contain sensitive PII and must be redacted in structured log outputs
SENSITIVE_LOG_KEYS = {
    "email",
    "recipient_email",
    "email_address",
    "phone",
    "phone_number",
    "mobile",
    "sms_number",
    "recipient_phone",
    "message",
    "raw_message",
    "body",
    "payload",
    "content",
    "first_name",
    "last_name",
    "full_name",
    "name",
    "address",
    "street",
}


class DispatcherError(Exception):
    """Base exception class for notification dispatcher domain operations."""

    pass


class DispatcherValidationError(DispatcherError):
    """Raised when notification dispatch parameters or payload inputs fail validation."""

    pass


class DispatchStatus(str, enum.Enum):
    """Enumeration of dispatch outcome statuses for individual notification channels."""

    DISPATCHED = "dispatched"
    OPTED_OUT = "opted_out"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ChannelDispatchResult:
    """Outcome result data container for a single notification delivery channel."""

    channel: str
    status: DispatchStatus
    reason: Optional[str] = None
    notification_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result container to dictionary representation for logging or serialization."""
        return {
            "channel": self.channel,
            "status": self.status.value,
            "reason": self.reason,
            "notification_id": self.notification_id,
        }


@dataclass
class DispatchSummary:
    """Summary container aggregating channel dispatch outcomes for a user notification request."""

    user_id: str
    category: NotificationCategory
    results: Dict[str, ChannelDispatchResult] = field(default_factory=dict)

    def is_any_dispatched(self) -> bool:
        """Check if any notification channel was successfully dispatched."""
        return any(
            res.status == DispatchStatus.DISPATCHED
            for res in self.results.values()
        )

    def is_all_opted_out(self) -> bool:
        """Check if all delivery channels were opted out by user preferences."""
        return all(
            res.status == DispatchStatus.OPTED_OUT
            for res in self.results.values()
        )

    def to_log_dict(self) -> Dict[str, Any]:
        """Produce a PII-redacted structured dictionary summary suitable for logging."""
        return {
            "user_id": self.user_id,
            "category": self.category.value,
            "channels_evaluated": list(self.results.keys()),
            "channel_statuses": {
                ch: res.status.value for ch, res in self.results.items()
            },
            "dispatched_count": sum(
                1 for res in self.results.values() if res.status == DispatchStatus.DISPATCHED
            ),
            "opted_out_count": sum(
                1 for res in self.results.values() if res.status == DispatchStatus.OPTED_OUT
            ),
        }


def redact_pii_for_logging(data: Any) -> Any:
    """Recursively sanitize data structures to strip PII keys and pattern matches for NFR-5 compliance.

    Omits email addresses, phone numbers, recipient names, and raw message bodies while
    preserving structural metadata like entity IDs, lengths, categories, and channels.

    Args:
        data: Arbitrary dictionary, list, string, or primitive to sanitize.

    Returns:
        Sanitized structure with PII attributes replaced or redacted.
    """
    if isinstance(data, dict):
        sanitized: Dict[str, Any] = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if key_lower in SENSITIVE_LOG_KEYS:
                if isinstance(value, str):
                    sanitized[key] = f"[REDACTED len={len(value)}]"
                else:
                    sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = redact_pii_for_logging(value)
        return sanitized

    if isinstance(data, list):
        return [redact_pii_for_logging(item) for item in data]

    if isinstance(data, tuple):
        return tuple(redact_pii_for_logging(item) for item in data)

    if isinstance(data, str):
        # Scrub inline emails and phone numbers from raw string messages
        scrubbed = EMAIL_REGEX.sub("[REDACTED_EMAIL]", data)
        scrubbed = E164_PHONE_SEARCH_REGEX.sub("[REDACTED_PHONE]", scrubbed)
        return scrubbed

    return data


def log_dispatch_event(
    event_message: str,
    summary_data: Dict[str, Any],
    level: int = logging.INFO,
) -> None:
    """Emit a structured PII-redacted log record for notification dispatch operations.

    Args:
        event_message: Descriptive log event message string.
        summary_data: Context dictionary containing dispatch operation parameters.
        level: Logging level integer (default: logging.INFO).
    """
    clean_message = redact_pii_for_logging(event_message)
    clean_data = redact_pii_for_logging(summary_data)
    logger.log(level, clean_message, extra={"dispatch_context": clean_data})


def evaluate_notification_channels(
    db: Session,
    user_id: str,
    category: Union[NotificationCategory, str],
) -> Dict[str, bool]:
    """Evaluate user notification preference matrix to determine enabled delivery channels.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: Target user identifier UUID string.
        category: Target notification category.

    Returns:
        Dictionary mapping channel names ('email', 'sms', 'in_app') to boolean enablement flags.

    Raises:
        DispatcherValidationError: If user_id or category inputs are invalid.
    """
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise DispatcherValidationError("User ID cannot be empty")

    try:
        category_enum = parse_notification_category(category)
    except PreferenceValidationError as err:
        raise DispatcherValidationError(str(err)) from err

    pref = get_user_preference_by_category(db, user_id.strip(), category_enum)

    sms_enabled = pref.channel_sms and bool(
        pref.phone_number and is_valid_e164_phone(pref.phone_number)
    )

    return {
        "email": bool(pref.channel_email),
        "sms": bool(sms_enabled),
        "in_app": bool(pref.channel_in_app),
    }


def dispatch_notification(
    db: Session,
    user_id: str,
    category: Union[NotificationCategory, str],
    title: str,
    message: str,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    email_sender: Optional[Callable[[str, str, str], bool]] = None,
    sms_sender: Optional[Callable[[str, str, str], bool]] = None,
    force_in_app: bool = False,
) -> DispatchSummary:
    """Evaluate user channel preferences and route notification delivery across active channels.

    Evaluates user notification preference matrix prior to dispatching alerts via email,
    SMS, or in-app channels (FR-10). Emits structured PII-redacted log records omitting
    recipient PII and raw message payloads (NFR-5).

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: Target user identifier UUID string.
        category: Notification category enum or string value.
        title: Notification summary header title string.
        message: Detailed notification message body string.
        email: Optional recipient email address string.
        phone_number: Optional recipient phone number in E.164 format.
        email_sender: Optional callable port for outbound email transmission (email, subject, body).
        sms_sender: Optional callable port for outbound SMS transmission (phone, body).
        force_in_app: If True, forces in-app notification creation regardless of preference setting.

    Returns:
        DispatchSummary object aggregating delivery status results across channels.

    Raises:
        DispatcherValidationError: If required payload parameters fail validation.
    """
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise DispatcherValidationError("User ID cannot be empty")

    if not title or not isinstance(title, str) or not title.strip():
        raise DispatcherValidationError("Notification title cannot be empty")

    if not message or not isinstance(message, str) or not message.strip():
        raise DispatcherValidationError("Notification message cannot be empty")

    try:
        category_enum = parse_notification_category(category)
    except PreferenceValidationError as err:
        raise DispatcherValidationError(str(err)) from err

    clean_user_id = user_id.strip()
    clean_title = title.strip()
    clean_message = message.strip()

    # Retrieve canonical user preference record for this category
    pref = get_user_preference_by_category(db, clean_user_id, category_enum)

    summary = DispatchSummary(
        user_id=clean_user_id,
        category=category_enum,
    )

    # --------------------------------------------------------------------------
    # 1. In-App Notification Channel Evaluation & Routing
    # --------------------------------------------------------------------------
    if pref.channel_in_app or force_in_app:
        try:
            created_notif = create_notification(
                db=db,
                user_id=clean_user_id,
                category=category_enum,
                title=clean_title,
                message=clean_message,
            )
            summary.results["in_app"] = ChannelDispatchResult(
                channel="in_app",
                status=DispatchStatus.DISPATCHED,
                reason="In-app notification successfully created and stored",
                notification_id=created_notif.id,
            )
        except Exception as err:
            logger.warning(
                "Failed to create in-app notification record",
                extra={"user_id": clean_user_id, "category": category_enum.value, "error": str(err)},
            )
            summary.results["in_app"] = ChannelDispatchResult(
                channel="in_app",
                status=DispatchStatus.FAILED,
                reason=f"In-app notification persistence error: {str(err)}",
            )
    else:
        summary.results["in_app"] = ChannelDispatchResult(
            channel="in_app",
            status=DispatchStatus.OPTED_OUT,
            reason="In-app channel disabled by user preference",
        )

    # --------------------------------------------------------------------------
    # 2. Email Channel Evaluation & Routing
    # --------------------------------------------------------------------------
    if pref.channel_email:
        if email_sender is not None:
            clean_email = email.strip() if email and email.strip() else None
            if clean_email:
                try:
                    success = email_sender(clean_email, clean_title, clean_message)
                    if success:
                        summary.results["email"] = ChannelDispatchResult(
                            channel="email",
                            status=DispatchStatus.DISPATCHED,
                            reason="Email sent successfully via gateway adapter",
                        )
                    else:
                        summary.results["email"] = ChannelDispatchResult(
                            channel="email",
                            status=DispatchStatus.FAILED,
                            reason="Email gateway adapter returned failure status",
                        )
                except Exception as err:
                    summary.results["email"] = ChannelDispatchResult(
                        channel="email",
                        status=DispatchStatus.FAILED,
                        reason=f"Email gateway exception: {str(err)}",
                    )
            else:
                summary.results["email"] = ChannelDispatchResult(
                    channel="email",
                    status=DispatchStatus.SKIPPED,
                    reason="Recipient email address was not provided",
                )
        else:
            summary.results["email"] = ChannelDispatchResult(
                channel="email",
                status=DispatchStatus.DISPATCHED,
                reason="Email channel enabled for user; ready for gateway dispatch",
            )
    else:
        summary.results["email"] = ChannelDispatchResult(
            channel="email",
            status=DispatchStatus.OPTED_OUT,
            reason="Email channel disabled by user preference",
        )

    # --------------------------------------------------------------------------
    # 3. SMS Channel Evaluation & Routing
    # --------------------------------------------------------------------------
    if pref.channel_sms:
        # Resolve target phone number from override or stored preference
        target_phone = phone_number.strip() if phone_number and phone_number.strip() else pref.phone_number

        if target_phone and is_valid_e164_phone(target_phone):
            if sms_sender is not None:
                try:
                    success = sms_sender(target_phone, clean_message)
                    if success:
                        summary.results["sms"] = ChannelDispatchResult(
                            channel="sms",
                            status=DispatchStatus.DISPATCHED,
                            reason="SMS sent successfully via gateway adapter",
                        )
                    else:
                        summary.results["sms"] = ChannelDispatchResult(
                            channel="sms",
                            status=DispatchStatus.FAILED,
                            reason="SMS gateway adapter returned failure status",
                        )
                except Exception as err:
                    summary.results["sms"] = ChannelDispatchResult(
                        channel="sms",
                        status=DispatchStatus.FAILED,
                        reason=f"SMS gateway exception: {str(err)}",
                    )
            else:
                summary.results["sms"] = ChannelDispatchResult(
                    channel="sms",
                    status=DispatchStatus.DISPATCHED,
                    reason="SMS channel enabled for user; ready for gateway dispatch",
                )
        else:
            summary.results["sms"] = ChannelDispatchResult(
                channel="sms",
                status=DispatchStatus.SKIPPED,
                reason="Missing or invalid E.164 phone number for SMS dispatch",
            )
    else:
        summary.results["sms"] = ChannelDispatchResult(
            channel="sms",
            status=DispatchStatus.OPTED_OUT,
            reason="SMS channel disabled by user preference",
        )

    # Emit PII-redacted structured log record for dispatch evaluation (NFR-5)
    log_dispatch_event(
        event_message=f"Evaluated notification dispatch for user {clean_user_id}",
        summary_data={
            "user_id": clean_user_id,
            "category": category_enum.value,
            "title_length": len(clean_title),
            "message_length": len(clean_message),
            "summary": summary.to_log_dict(),
            "results": [res.to_dict() for res in summary.results.values()],
        },
    )

    return summary
