"""Unit tests for FastAPI request dependencies in backend/src/api/deps.py."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session as DBSession

from src.api.deps import (
    extract_session_id,
    get_current_session,
    get_current_tenant_id,
    get_current_user,
    verify_tenant_isolation,
)
from src.lib.config import settings
from src.models.base import Base, create_db_engine
from src.models.session import Session
from src.models.tenant import Tenant
from src.models.user import User
from src.services.auth.service import create_session, register_user


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


def make_request(cookies=None, headers=None, path="/api/v1/protected") -> Request:
    """Helper to construct FastAPI Request instances with specific cookies and headers."""
    raw_headers = []
    if headers:
        for key, val in headers.items():
            raw_headers.append((key.lower().encode("latin-1"), str(val).encode("latin-1")))

    cookie_header_parts = []
    if cookies:
        for key, val in cookies.items():
            cookie_header_parts.append(f"{key}={val}")
        raw_headers.append((b"cookie", "; ".join(cookie_header_parts).encode("latin-1")))

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
    }
    return Request(scope)


def test_extract_session_id_from_cookie():
    """Verify session ID extraction from HTTP cookie."""
    req = make_request(cookies={settings.SESSION_COOKIE_NAME: "cookie-session-token-123"})
    token = extract_session_id(req)
    assert token == "cookie-session-token-123"


def test_extract_session_id_from_x_session_id_header():
    """Verify session ID extraction from X-Session-ID header."""
    req = make_request(headers={"X-Session-ID": "header-session-token-456"})
    token = extract_session_id(req)
    assert token == "header-session-token-456"


def test_extract_session_id_from_authorization_bearer():
    """Verify session ID extraction from Authorization: Bearer header."""
    req = make_request(headers={"Authorization": "Bearer bearer-session-token-789"})
    token = extract_session_id(req)
    assert token == "bearer-session-token-789"


def test_extract_session_id_from_session_id_header():
    """Verify session ID extraction from session_id header."""
    req = make_request(headers={"session_id": "direct-session-token-abc"})
    token = extract_session_id(req)
    assert token == "direct-session-token-abc"


def test_extract_session_id_none_when_missing():
    """Verify extract_session_id returns None when no token is present."""
    req = make_request()
    token = extract_session_id(req)
    assert token is None


def test_get_current_session_success(test_db):
    """Verify get_current_session returns active session for valid token."""
    user = register_user(
        db=test_db,
        email="testuser@example.com",
        password="Password123!",
        full_name="Test User",
        tenant_name="Test Tenant",
    )
    session_rec = create_session(db=test_db, user_id=user.id, tenant_id=user.tenant_id)

    req = make_request(cookies={settings.SESSION_COOKIE_NAME: session_rec.id})
    active_session = get_current_session(request=req, db=test_db)

    assert active_session is not None
    assert active_session.id == session_rec.id
    assert active_session.user_id == user.id
    assert active_session.tenant_id == user.tenant_id


def test_get_current_session_missing_token_raises_401(test_db):
    """Verify get_current_session raises 401 when token is missing."""
    req = make_request()
    with pytest.raises(HTTPException) as exc_info:
        get_current_session(request=req, db=test_db)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"


def test_get_current_session_invalid_token_raises_401(test_db):
    """Verify get_current_session raises 401 when token is invalid or non-existent."""
    req = make_request(cookies={settings.SESSION_COOKIE_NAME: "non-existent-token"})
    with pytest.raises(HTTPException) as exc_info:
        get_current_session(request=req, db=test_db)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"


def test_get_current_session_expired_token_raises_401(test_db):
    """Verify get_current_session raises 401 when session is expired."""
    user = register_user(
        db=test_db,
        email="expireduser@example.com",
        password="Password123!",
        full_name="Expired User",
        tenant_name="Test Tenant",
    )
    # Create expired session
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session_rec = Session(
        user_id=user.id,
        tenant_id=user.tenant_id,
        expires_at=expired_at,
    )
    test_db.add(session_rec)
    test_db.commit()

    req = make_request(cookies={settings.SESSION_COOKIE_NAME: session_rec.id})
    with pytest.raises(HTTPException) as exc_info:
        get_current_session(request=req, db=test_db)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"


def test_get_current_user_success(test_db):
    """Verify get_current_user returns UserModel bound to session."""
    user = register_user(
        db=test_db,
        email="user_dep@example.com",
        password="Password123!",
        full_name="Dep User",
        tenant_name="Dep Tenant",
    )
    session_rec = create_session(db=test_db, user_id=user.id, tenant_id=user.tenant_id)

    curr_user = get_current_user(session=session_rec)
    assert curr_user.id == user.id
    assert curr_user.email == "user_dep@example.com"


def test_get_current_user_inactive_raises_401(test_db):
    """Verify get_current_user raises 401 if user is inactive."""
    user = register_user(
        db=test_db,
        email="inactive@example.com",
        password="Password123!",
        full_name="Inactive User",
        tenant_name="Dep Tenant",
    )
    session_rec = create_session(db=test_db, user_id=user.id, tenant_id=user.tenant_id)
    user.is_active = False
    test_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(session=session_rec)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"


def test_get_current_tenant_id_success(test_db):
    """Verify get_current_tenant_id returns session tenant ID."""
    user = register_user(
        db=test_db,
        email="tenantuser@example.com",
        password="Password123!",
        full_name="Tenant User",
        tenant_name="Tenant Acme",
    )
    session_rec = create_session(db=test_db, user_id=user.id, tenant_id=user.tenant_id)

    tenant_id = get_current_tenant_id(session=session_rec)
    assert tenant_id == user.tenant_id


def test_verify_tenant_isolation_success(test_db):
    """Verify verify_tenant_isolation succeeds when session tenant matches target tenant."""
    user = register_user(
        db=test_db,
        email="iso_user@example.com",
        password="Password123!",
        full_name="Iso User",
        tenant_name="Iso Tenant",
    )
    session_rec = create_session(db=test_db, user_id=user.id, tenant_id=user.tenant_id)

    validated_tenant = verify_tenant_isolation(
        target_tenant_id=user.tenant_id,
        session=session_rec,
    )
    assert validated_tenant == user.tenant_id


def test_verify_tenant_isolation_mismatch_raises_403(test_db):
    """Verify verify_tenant_isolation raises 403 TENANT_MISMATCH when tenant IDs differ."""
    user = register_user(
        db=test_db,
        email="iso_mismatch@example.com",
        password="Password123!",
        full_name="Iso Mismatch User",
        tenant_name="Iso Tenant 1",
    )
    session_rec = create_session(db=test_db, user_id=user.id, tenant_id=user.tenant_id)

    with pytest.raises(HTTPException) as exc_info:
        verify_tenant_isolation(
            target_tenant_id="other-tenant-id-999",
            session=session_rec,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail["code"] == "TENANT_MISMATCH"
