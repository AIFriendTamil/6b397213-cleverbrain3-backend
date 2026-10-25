"""Domain service for user notification preference matrix management with E.164 phone validation."""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.notification import (
    NotificationCategory,
    NotificationPreference,
)

logger = logging.getLogger(__name__)

E164_PHONE_REGEX = re.compile(r"^\+[1-9]\d{1,14}$")


class PreferenceServiceError(Exception):
    """Base exception class for notification preference domain service operations."""

    pass


class PreferenceValidationError(PreferenceServiceError):
    """Raised when notification preference inputs or phone numbers fail validation."""

    pass


class PreferenceNotFoundError(PreferenceServiceError):
    """Raised when a requested notification preference record cannot be found."""

    pass


def is_valid_e164_phone(phone_number: Optional[str]) -> bool:
    """Validate whether a given string adheres to international E.164 phone format.

    E.164 numbers start with a plus sign '+', followed by country code and subscriber number,
    containing 2 to 15 digits total without spaces, dashes, or special characters.

    Args:
        phone_number: Phone string to validate.

    Returns:
        True if phone_number is a valid E.164 string, False otherwise.
    """
    if not phone_number or not isinstance(phone_number, str):
        return False
    return bool(E164_PHONE_REGEX.match(phone_number.strip()))


def parse_notification_category(
    category_input: Union[NotificationCategory, str]
) -> NotificationCategory:
    """Parse and validate a category input into a NotificationCategory enum value.

    Args:
        category_input: NotificationCategory enum instance or string representation.

    Returns:
        Validated NotificationCategory enum value.

    Raises:
        PreferenceValidationError: If category_input is invalid or unmapped.
    """
    if isinstance(category_input, NotificationCategory):
        return category_input

    if not isinstance(category_input, str) or not category_input.strip():
        raise PreferenceValidationError("Notification category input cannot be empty")

    cleaned = category_input.strip()
    for enum_val in NotificationCategory:
        if cleaned.lower() in (enum_val.value.lower(), enum_val.name.lower()):
            return enum_val

    valid_categories = [e.value for e in NotificationCategory]
    raise PreferenceValidationError(
        f"Invalid notification category '{category_input}'. Must be one of {valid_categories}"
    )


def get_user_notification_preferences(
    db: Session,
    user_id: str,
) -> List[NotificationPreference]:
    """Retrieve all notification preference records for a user across all categories.

    If preference records do not exist for certain categories, default preference
    entities are created, persisted, and returned so that a complete matrix is always available.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.

    Returns:
        List of NotificationPreference model instances for all categories.

    Raises:
        PreferenceValidationError: If user_id is empty or invalid.
    """
    if not user_id or not user_id.strip():
        raise PreferenceValidationError("User ID cannot be empty")

    clean_user_id = user_id.strip()
    stmt = select(NotificationPreference).where(
        NotificationPreference.user_id == clean_user_id
    )
    existing_prefs = list(db.scalars(stmt).all())
    existing_by_category = {pref.category: pref for pref in existing_prefs}

    created_any = False
    for category in NotificationCategory:
        if category not in existing_by_category:
            new_pref = NotificationPreference(
                user_id=clean_user_id,
                category=category,
                channel_email=True,
                channel_sms=False,
                channel_in_app=True,
                phone_number=None,
            )
            db.add(new_pref)
            existing_prefs.append(new_pref)
            created_any = True

    if created_any:
        db.commit()
        for pref in existing_prefs:
            db.refresh(pref)

    category_order = {cat: i for i, cat in enumerate(NotificationCategory)}
    existing_prefs.sort(key=lambda p: category_order.get(p.category, 99))
    return existing_prefs


def get_user_preference_by_category(
    db: Session,
    user_id: str,
    category: Union[NotificationCategory, str],
) -> NotificationPreference:
    """Retrieve or initialize a single NotificationPreference record for a user and category.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: Target notification category.

    Returns:
        NotificationPreference model instance.

    Raises:
        PreferenceValidationError: If user_id or category is invalid.
    """
    if not user_id or not user_id.strip():
        raise PreferenceValidationError("User ID cannot be empty")

    clean_user_id = user_id.strip()
    cat_enum = parse_notification_category(category)

    stmt = select(NotificationPreference).where(
        NotificationPreference.user_id == clean_user_id,
        NotificationPreference.category == cat_enum,
    )
    pref = db.scalars(stmt).first()

    if not pref:
        pref = NotificationPreference(
            user_id=clean_user_id,
            category=cat_enum,
            channel_email=True,
            channel_sms=False,
            channel_in_app=True,
            phone_number=None,
        )
        db.add(pref)
        db.commit()
        db.refresh(pref)

    return pref


def update_user_notification_preference(
    db: Session,
    user_id: str,
    category: Union[NotificationCategory, str],
    channel_email: Optional[bool] = None,
    channel_sms: Optional[bool] = None,
    channel_in_app: Optional[bool] = None,
    phone_number: Optional[str] = None,
) -> NotificationPreference:
    """Update notification preference settings for a given user and category with E.164 phone validation.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: Target notification category.
        channel_email: Optional boolean toggle for email notifications.
        channel_sms: Optional boolean toggle for SMS notifications.
        channel_in_app: Optional boolean toggle for in-app notifications.
        phone_number: Optional phone number string.

    Returns:
        Updated NotificationPreference model instance.

    Raises:
        PreferenceValidationError: If inputs or phone numbers fail E.164 validation.
    """
    if not user_id or not user_id.strip():
        raise PreferenceValidationError("User ID cannot be empty")

    clean_user_id = user_id.strip()
    cat_enum = parse_notification_category(category)
    pref = get_user_preference_by_category(db, clean_user_id, cat_enum)

    effective_channel_sms = channel_sms if channel_sms is not None else pref.channel_sms

    # Normalize phone_number string
    if phone_number is not None:
        clean_phone = phone_number.strip() if phone_number.strip() else None
    else:
        clean_phone = pref.phone_number

    if effective_channel_sms:
        if not clean_phone:
            raise PreferenceValidationError(
                "Phone number in valid E.164 format is required when SMS channel is enabled"
            )
        if not is_valid_e164_phone(clean_phone):
            raise PreferenceValidationError(
                f"Invalid E.164 phone number format: '{clean_phone}'. "
                "Expected format '+<country_code><number>' (e.g. +14155552671)."
            )
    elif clean_phone is not None:
        if not is_valid_e164_phone(clean_phone):
            raise PreferenceValidationError(
                f"Invalid E.164 phone number format: '{clean_phone}'. "
                "Expected format '+<country_code><number>' (e.g. +14155552671)."
            )

    if channel_email is not None:
        pref.channel_email = channel_email
    if channel_sms is not None:
        pref.channel_sms = channel_sms
    if channel_in_app is not None:
        pref.channel_in_app = channel_in_app
    if phone_number is not None:
        pref.phone_number = clean_phone

    pref.updated_at = datetime.now(timezone.utc)
    db.add(pref)
    db.commit()
    db.refresh(pref)

    return pref


def update_user_notification_preference_matrix(
    db: Session,
    user_id: str,
    preferences_data: List[Union[Dict[str, Any], Any]],
) -> List[NotificationPreference]:
    """Bulk update user notification preference matrix across multiple categories.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        preferences_data: List of preference dicts or objects containing category and channel toggles.

    Returns:
        List of updated NotificationPreference model instances for all categories.

    Raises:
        PreferenceValidationError: If input format is invalid or validation fails for any entry.
    """
    if not user_id or not user_id.strip():
        raise PreferenceValidationError("User ID cannot be empty")

    if not isinstance(preferences_data, list):
        raise PreferenceValidationError("Preferences data must be a list of preference settings")

    for item in preferences_data:
        if isinstance(item, dict):
            cat = item.get("category")
            c_email = item.get("channel_email")
            c_sms = item.get("channel_sms")
            c_in_app = item.get("channel_in_app")
            p_num = item.get("phone_number")
        else:
            cat = getattr(item, "category", None)
            c_email = getattr(item, "channel_email", None)
            c_sms = getattr(item, "channel_sms", None)
            c_in_app = getattr(item, "channel_in_app", None)
            p_num = getattr(item, "phone_number", None)

        if cat is None:
            raise PreferenceValidationError("Each preference item must specify a category")

        update_user_notification_preference(
            db=db,
            user_id=user_id,
            category=cat,
            channel_email=c_email,
            channel_sms=c_sms,
            channel_in_app=c_in_app,
            phone_number=p_num,
        )

    return get_user_notification_preferences(db, user_id)


def is_channel_enabled_for_user(
    db: Session,
    user_id: str,
    category: Union[NotificationCategory, str],
    channel: str,
) -> bool:
    """Evaluate whether a specific delivery channel is enabled for a given user and category.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: User identifier UUID string.
        category: Target notification category.
        channel: Channel name ('email', 'sms', or 'in_app').

    Returns:
        True if channel is enabled (and phone number valid for SMS), False otherwise.

    Raises:
        PreferenceValidationError: If category or channel name is unmapped.
    """
    if not user_id or not user_id.strip() or not channel or not channel.strip():
        return False

    cat_enum = parse_notification_category(category)
    pref = get_user_preference_by_category(db, user_id, cat_enum)

    clean_channel = channel.strip().lower().replace("-", "_")
    if clean_channel == "email":
        return pref.channel_email
    elif clean_channel == "sms":
        if not pref.channel_sms:
            return False
        return bool(pref.phone_number and is_valid_e164_phone(pref.phone_number))
    elif clean_channel in ("in_app", "inapp"):
        return pref.channel_in_app
    else:
        raise PreferenceValidationError(f"Unknown channel: '{channel}'")
