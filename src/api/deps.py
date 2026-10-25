"""FastAPI request dependencies for database sessions, authentication, and tenant isolation."""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DBSession

from src.lib.config import settings
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.models.user import User as UserModel
from src.services.auth.service import (
    AuthServiceError,
    TenantMismatchError,
    UnauthorizedError,
    get_active_session,
    validate_tenant_access,
)

logger = logging.getLogger(__name__)


def extract_session_id(request: Request) -> Optional[str]:
    """Extract session token string from HTTP request cookies or headers.

    Checks sources in priority order:
    1. HTTP Cookie (named settings.SESSION_COOKIE_NAME, default 'session_id')
    2. Header 'X-Session-ID' or 'x-session-id'
    3. Header 'Authorization' (Bearer token)
    4. Header 'session_id' or 'session-id'

    Args:
        request: Incoming FastAPI Request object.

    Returns:
        Session ID token string if present, else None.
    """
    # 1. Cookie
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_id:
        return session_id.strip()

    # 2. X-Session-ID header
    session_id = request.headers.get("X-Session-ID") or request.headers.get("x-session-id")
    if session_id:
        return session_id.strip()

    # 3. Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        parts = auth_header.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()

    # 4. session_id header
    session_id = request.headers.get("session_id") or request.headers.get("session-id")
    if session_id:
        return session_id.strip()

    return None


def get_current_session(
    request: Request,
    db: DBSession = Depends(get_db),
) -> SessionModel:
    """FastAPI dependency that extracts and validates the active session from request context.

    Args:
        request: Incoming FastAPI Request object.
        db: Synchronous SQLAlchemy database session.

    Returns:
        Active Session ORM entity with loaded user and tenant relationships.

    Raises:
        HTTPException: HTTP 401 Unauthorized if session token is missing, invalid, or expired.
    """
    session_id = extract_session_id(request)
    if not session_id:
        logger.warning("Unauthenticated request: missing session token from %s", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication required. Missing session token.",
                "details": {},
            },
        )

    try:
        session = get_active_session(db, session_id)
        return session
    except UnauthorizedError as err:
        logger.warning("Session validation failed for path %s: %s", request.url.path, err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": str(err),
                "details": {},
            },
        )
    except AuthServiceError as err:
        logger.warning("Auth service error during session lookup: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": str(err),
                "details": {},
            },
        )


def get_current_user(
    session: SessionModel = Depends(get_current_session),
) -> UserModel:
    """FastAPI dependency that provides the authenticated user bound to current session.

    Args:
        session: Active user session dependency.

    Returns:
        Authenticated User ORM entity.

    Raises:
        HTTPException: HTTP 401 Unauthorized if associated user account is missing or inactive.
    """
    if session.user is None or not session.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Associated user account is missing or inactive.",
                "details": {},
            },
        )
    return session.user


def get_current_tenant_id(
    session: SessionModel = Depends(get_current_session),
) -> str:
    """FastAPI dependency that extracts authoritative server-managed tenant ID from current session.

    Client-provided tenant parameters are NEVER trusted; tenant context is derived strictly
    from the server-managed database session.

    Args:
        session: Active user session dependency.

    Returns:
        Authoritative tenant ID string.

    Raises:
        HTTPException: HTTP 401 Unauthorized if session lacks a valid tenant ID.
    """
    if not session.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Session context does not contain a valid tenant binding.",
                "details": {},
            },
        )
    return session.tenant_id


def verify_tenant_isolation(
    target_tenant_id: str,
    session: SessionModel = Depends(get_current_session),
) -> str:
    """Enforce strict server-side tenant boundary isolation.

    Validates that the active session's tenant matches the requested resource tenant ID.

    Args:
        target_tenant_id: Tenant ID of the target resource or operation.
        session: Active user session dependency.

    Returns:
        Validated tenant ID string.

    Raises:
        HTTPException: HTTP 403 Forbidden if session tenant does not match target tenant.
    """
    try:
        validate_tenant_access(session, target_tenant_id)
        return session.tenant_id
    except TenantMismatchError as err:
        logger.warning(
            "Tenant isolation violation: session tenant %s != target tenant %s",
            session.tenant_id,
            target_tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TENANT_MISMATCH",
                "message": str(err),
                "details": {
                    "session_tenant_id": session.tenant_id,
                    "target_tenant_id": target_tenant_id,
                },
            },
        )
