"""FastAPI router for user notification preferences matrix management and in-app notification hub operations."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import extract_session_id
from src.models.base import get_db
from src.models.notification import Notification, NotificationPreference
from src.services.auth.service import get_active_session
from src.services.notifications.hub import (
    NotificationNotFoundError,
    NotificationValidationError,
    delete_notification,
    get_user_notifications,
    mark_all_notifications_as_read,
    update_notification_read_status,
)
from src.services.notifications.preference import (
    PreferenceNotFoundError,
    PreferenceValidationError,
    get_user_notification_preferences,
    update_user_notification_preference_matrix,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


# --- Schemas ---


class NotificationPreferenceResponseSchema(BaseModel):
    """Pydantic schema for a notification preference matrix entry response."""

    id: str = Field(..., description="Unique preference record UUID")
    user_id: str = Field(..., description="User UUID")
    category: str = Field(..., description="Notification category (Financial, Milestone, SLA)")
    channel_email: bool = Field(..., description="Email channel toggle")
    channel_sms: bool = Field(..., description="SMS channel toggle")
    channel_in_app: bool = Field(..., description="In-app channel toggle")
    phone_number: Optional[str] = Field(None, description="Phone number in E.164 format (e.g. +14155552671)")
    updated_at: str = Field(..., description="ISO 8601 timestamp when preference was last updated")

    model_config = ConfigDict(from_attributes=True)


class NotificationPreferenceUpdateSchema(BaseModel):
    """Pydantic schema for updating a category preference entry."""

    category: str = Field(..., description="Notification category (Financial, Milestone, SLA)")
    channel_email: Optional[bool] = Field(None, description="Optional email channel toggle")
    channel_sms: Optional[bool] = Field(None, description="Optional SMS channel toggle")
    channel_in_app: Optional[bool] = Field(None, description="Optional in-app channel toggle")
    phone_number: Optional[str] = Field(None, description="Optional phone number in E.164 format")


class NotificationPreferenceMatrixUpdateSchema(BaseModel):
    """Pydantic schema for bulk updating the preferences matrix."""

    preferences: List[NotificationPreferenceUpdateSchema] = Field(
        ..., description="List of category preference settings to update"
    )


class NotificationResponseSchema(BaseModel):
    """Pydantic schema for an in-app notification hub item."""

    id: str = Field(..., description="Unique notification UUID")
    user_id: str = Field(..., description="User UUID")
    category: str = Field(..., description="Notification category (Financial, Milestone, SLA)")
    title: str = Field(..., description="Notification summary header string")
    message: str = Field(..., description="Detailed notification message payload")
    is_read: bool = Field(..., description="Read status flag")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")

    model_config = ConfigDict(from_attributes=True)


class NotificationReadUpdateSchema(BaseModel):
    """Pydantic schema for updating a notification read status."""

    is_read: bool = Field(True, description="Target read status boolean (default: True)")


class MarkAllReadRequestSchema(BaseModel):
    """Pydantic schema for requesting bulk mark-all-read with optional category filter."""

    category: Optional[str] = Field(None, description="Optional category filter (Financial, Milestone, SLA)")


class MarkAllReadResponseSchema(BaseModel):
    """Pydantic schema for mark-all-read operation response."""

    marked_count: int = Field(..., description="Total count of notifications updated to read status")
    message: str = Field(..., description="Human-readable summary message")


# --- Helper Functions ---


def serialize_preference(pref: NotificationPreference) -> Dict[str, Any]:
    """Convert a NotificationPreference ORM model to a dictionary suitable for NotificationPreferenceResponseSchema."""
    cat_str = pref.category.value if hasattr(pref.category, "value") else str(pref.category)
    updated_str = (
        pref.updated_at.isoformat()
        if hasattr(pref.updated_at, "isoformat")
        else str(pref.updated_at)
    )
    return {
        "id": pref.id,
        "user_id": pref.user_id,
        "category": cat_str,
        "channel_email": pref.channel_email,
        "channel_sms": pref.channel_sms,
        "channel_in_app": pref.channel_in_app,
        "phone_number": pref.phone_number,
        "updated_at": updated_str,
    }


def serialize_notification(notification: Notification) -> Dict[str, Any]:
    """Convert a Notification ORM model to a dictionary suitable for NotificationResponseSchema."""
    cat_str = (
        notification.category.value
        if hasattr(notification.category, "value")
        else str(notification.category)
    )
    created_str = (
        notification.created_at.isoformat()
        if hasattr(notification.created_at, "isoformat")
        else str(notification.created_at)
    )
    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "category": cat_str,
        "title": notification.title,
        "message": notification.message,
        "is_read": notification.is_read,
        "created_at": created_str,
    }


def get_current_user_id(
    request: Request,
    db: DBSession = Depends(get_db),
) -> str:
    """Extract authenticated user ID from active session or context headers."""
    session_id = extract_session_id(request)
    if session_id:
        try:
            session = get_active_session(db, session_id)
            if session and session.user_id:
                return session.user_id
        except Exception:
            pass

    user_id_hdr = (
        request.headers.get("X-User-ID")
        or request.headers.get("x-user-id")
        or request.query_params.get("user_id")
    )
    if user_id_hdr and user_id_hdr.strip():
        return user_id_hdr.strip()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "UNAUTHORIZED",
            "message": "Authentication required. Missing session token or user context.",
            "details": {},
        },
    )


# --- Endpoint Handlers ---


@router.get(
    "/preferences",
    response_model=List[NotificationPreferenceResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="Get user notification preference matrix",
    description="Retrieve notification channel preferences for the authenticated user across Financial, Milestone, and SLA categories.",
)
def get_preferences_endpoint(
    db: DBSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> List[Dict[str, Any]]:
    """Retrieve all notification preferences for the authenticated user."""
    try:
        prefs = get_user_notification_preferences(db=db, user_id=user_id)
        return [serialize_preference(p) for p in prefs]
    except PreferenceValidationError as err:
        logger.warning("Failed to retrieve preferences for user %s: %s", user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        )


@router.put(
    "/preferences",
    response_model=List[NotificationPreferenceResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="Update user notification preference matrix",
    description="Update multi-channel preferences across categories with E.164 phone format validation.",
)
def update_preferences_endpoint(
    request: Request,
    payload: Any = Body(...),
    db: DBSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> List[Dict[str, Any]]:
    """Update user notification channel preference matrix."""
    items = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if "preferences" in payload and isinstance(payload["preferences"], list):
            items = payload["preferences"]
        else:
            items = [payload]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Payload must be a list of preferences or an object containing a 'preferences' array.",
                "details": {},
            },
        )

    try:
        updated_prefs = update_user_notification_preference_matrix(
            db=db,
            user_id=user_id,
            preferences_data=items,
        )
        return [serialize_preference(p) for p in updated_prefs]
    except PreferenceValidationError as err:
        logger.warning("Preference validation failure for user %s: %s", user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        )
    except PreferenceNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )


@router.get(
    "/hub",
    response_model=List[NotificationResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="Retrieve central notification hub alerts",
    description="Get in-app notifications for the authenticated user, with optional category and read status filters.",
)
def get_notifications_endpoint(
    category: Optional[str] = Query(None, description="Optional category filter (Financial, Milestone, SLA)"),
    is_read: Optional[bool] = Query(None, description="Optional read status filter"),
    db: DBSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> List[Dict[str, Any]]:
    """Retrieve notifications for the authenticated user."""
    try:
        notifications = get_user_notifications(
            db=db,
            user_id=user_id,
            category=category,
            is_read=is_read,
        )
        return [serialize_notification(n) for n in notifications]
    except NotificationValidationError as err:
        logger.warning("Failed to retrieve notifications for user %s: %s", user_id, err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        )


@router.patch(
    "/hub/{notification_id}/read",
    response_model=NotificationResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Toggle notification read state",
    description="Update the read/unread state of an in-app notification by ID.",
)
def update_notification_read_endpoint(
    notification_id: str,
    payload: Optional[NotificationReadUpdateSchema] = Body(default=None),
    is_read: Optional[bool] = Query(None, description="Optional query param read status toggle"),
    db: DBSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Update read state for a specific notification."""
    target_is_read = True
    if payload is not None and hasattr(payload, "is_read") and payload.is_read is not None:
        target_is_read = payload.is_read
    elif is_read is not None:
        target_is_read = is_read

    try:
        updated_notification = update_notification_read_status(
            db=db,
            notification_id=notification_id,
            is_read=target_is_read,
            user_id=user_id,
        )
        return serialize_notification(updated_notification)
    except NotificationNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except NotificationValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        )


@router.post(
    "/hub/mark-all-read",
    response_model=MarkAllReadResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Mark all unread notifications as read",
    description="Bulk update all unread notifications to read status for the authenticated user.",
)
def mark_all_read_endpoint(
    payload: Optional[MarkAllReadRequestSchema] = Body(default=None),
    category: Optional[str] = Query(None, description="Optional category filter"),
    db: DBSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Bulk mark unread notifications as read."""
    target_category = category
    if target_category is None and payload is not None and payload.category:
        target_category = payload.category

    try:
        marked_count = mark_all_notifications_as_read(
            db=db,
            user_id=user_id,
            category=target_category,
        )
        return {
            "marked_count": marked_count,
            "message": f"Successfully marked {marked_count} notification(s) as read.",
        }
    except NotificationValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        )


@router.delete(
    "/hub/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete in-app notification",
    description="Permanently delete an in-app notification record by ID.",
)
def delete_notification_endpoint(
    notification_id: str,
    db: DBSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete an in-app notification record."""
    try:
        delete_notification(
            db=db,
            notification_id=notification_id,
            user_id=user_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except NotificationNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": str(err),
                "details": {},
            },
        )
    except NotificationValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        )
