"""HTTP Integration tests for authentication API endpoints in backend/src/api/routes/auth.py."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.lib.config import settings
from src.models.base import Base, create_db_engine, get_db
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.user import User as UserModel
from src.services.auth.service import (
    AuthService,
    TenantMismatchError,
    get_active_session,
    validate_tenant_access,
)


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def client(test_db):
    """Fixture providing a FastAPI TestClient with database session dependency overridden."""
    app = create_app()

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- User Registration Integration Tests ---


def test_register_user_success_integration(client, test_db):
    """Verify POST /api/v1/auth/register creates user and tenant in DB and returns 201 Created."""
    payload = {
        "email": "integration_user@example.com",
        "password": "SecurePassword123!",
        "full_name": "Integration User",
        "tenant_name": "Integration Corp",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == status.HTTP_201_CREATED

    data = response.json()
    assert data["email"] == "integration_user@example.com"
    assert data["full_name"] == "Integration User"
    assert "user_id" in data
    assert "tenant_id" in data

    # Verify database persistence
    user_in_db = test_db.query(UserModel).filter_by(id=data["user_id"]).first()
    assert user_in_db is not None
    assert user_in_db.email == "integration_user@example.com"
    assert user_in_db.full_name == "Integration User"
    assert user_in_db.is_active is True

    tenant_in_db = test_db.query(TenantModel).filter_by(id=data["tenant_id"]).first()
    assert tenant_in_db is not None
    assert tenant_in_db.name == "Integration Corp"


def test_register_user_duplicate_email_conflict_integration(client):
    """Verify registration with an existing email returns 409 Conflict with ACCOUNT_EXISTS."""
    payload = {
        "email": "duplicate_check@example.com",
        "password": "Password123!",
        "full_name": "First Register",
        "tenant_name": "Tenant One",
    }
    res1 = client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED

    # Attempt registration with duplicate email (uppercase check)
    dup_payload = {
        "email": "DUPLICATE_CHECK@EXAMPLE.COM",
        "password": "OtherPassword123!",
        "full_name": "Second Register",
        "tenant_name": "Tenant Two",
    }
    res2 = client.post("/api/v1/auth/register", json=dup_payload)
    assert res2.status_code == status.HTTP_409_CONFLICT

    err_data = res2.json()
    assert "detail" in err_data
    assert err_data["detail"]["code"] == "ACCOUNT_EXISTS"


def test_register_user_invalid_inputs_integration(client):
    """Verify registration with missing or empty fields returns 400 Bad Request."""
    invalid_payloads = [
        {"email": "", "password": "Password123!", "full_name": "Name", "tenant_name": "Tenant"},
        {"email": "valid@example.com", "password": "", "full_name": "Name", "tenant_name": "Tenant"},
        {"email": "valid@example.com", "password": "Password123!", "full_name": "  ", "tenant_name": "Tenant"},
        {"email": "valid@example.com", "password": "Password123!", "full_name": "Name", "tenant_name": ""},
    ]

    for payload in invalid_payloads:
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        err_data = response.json()
        assert err_data["detail"]["code"] == "INVALID_INPUT"


def test_register_user_existing_tenant_reuse_integration(client, test_db):
    """Verify registering a second user with the same tenant name reuses the existing tenant."""
    payload1 = {
        "email": "user1@sharedtenant.com",
        "password": "Password123!",
        "full_name": "User One",
        "tenant_name": "Shared Tenant Org",
    }
    res1 = client.post("/api/v1/auth/register", json=payload1)
    assert res1.status_code == status.HTTP_201_CREATED
    tenant_id1 = res1.json()["tenant_id"]

    payload2 = {
        "email": "user2@sharedtenant.com",
        "password": "Password123!",
        "full_name": "User Two",
        "tenant_name": "Shared Tenant Org",
    }
    res2 = client.post("/api/v1/auth/register", json=payload2)
    assert res2.status_code == status.HTTP_201_CREATED
    tenant_id2 = res2.json()["tenant_id"]

    assert tenant_id1 == tenant_id2

    tenant_count = test_db.query(TenantModel).filter_by(name="Shared Tenant Org").count()
    assert tenant_count == 1


# --- User Authentication & Login Integration Tests ---


def test_login_success_integration(client, test_db):
    """Verify POST /api/v1/auth/login sets session cookie, header, and session record in DB."""
    # Register user
    reg_payload = {
        "email": "login_user@example.com",
        "password": "CorrectPassword123!",
        "full_name": "Login User",
        "tenant_name": "Login Org",
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == status.HTTP_201_CREATED

    login_payload = {
        "email": "login_user@example.com",
        "password": "CorrectPassword123!",
    }
    response = client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["status"] == "success"
    assert data["user"]["email"] == "login_user@example.com"
    assert data["user"]["full_name"] == "Login User"
    assert "id" in data["user"]
    assert "tenant_id" in data["user"]

    # Verify cookie and response header
    assert settings.SESSION_COOKIE_NAME in response.cookies
    session_id = response.cookies[settings.SESSION_COOKIE_NAME]
    assert response.headers.get("X-Session-ID") == session_id

    # Verify session in DB
    session_in_db = test_db.query(SessionModel).filter_by(id=session_id).first()
    assert session_in_db is not None
    assert session_in_db.user_id == data["user"]["id"]
    assert session_in_db.tenant_id == data["user"]["tenant_id"]


def test_login_invalid_password_integration(client):
    """Verify login with invalid password returns 401 Unauthorized with INVALID_CREDENTIALS."""
    reg_payload = {
        "email": "wrong_pass_user@example.com",
        "password": "RightPassword123!",
        "full_name": "Pass Test User",
        "tenant_name": "Pass Org",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_payload = {
        "email": "wrong_pass_user@example.com",
        "password": "WrongPassword123!",
    }
    response = client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = response.json()
    assert err_data["detail"]["code"] == "INVALID_CREDENTIALS"


def test_login_nonexistent_email_integration(client):
    """Verify login with non-existent email returns 401 Unauthorized with INVALID_CREDENTIALS."""
    login_payload = {
        "email": "nonexistent@example.com",
        "password": "Password123!",
    }
    response = client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = response.json()
    assert err_data["detail"]["code"] == "INVALID_CREDENTIALS"


def test_login_inactive_user_integration(client, test_db):
    """Verify login for deactivated user account returns 401 Unauthorized."""
    reg_payload = {
        "email": "inactive_user@example.com",
        "password": "Password123!",
        "full_name": "Inactive User",
        "tenant_name": "Inactive Org",
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    user_id = reg_res.json()["user_id"]

    # Deactivate user account in DB
    user = test_db.query(UserModel).filter_by(id=user_id).first()
    user.is_active = False
    test_db.commit()

    login_payload = {
        "email": "inactive_user@example.com",
        "password": "Password123!",
    }
    response = client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = response.json()
    assert err_data["detail"]["code"] == "INVALID_CREDENTIALS"


# --- Authenticated User Profile Endpoint Integration Tests ---


def test_get_me_via_session_cookie(client):
    """Verify GET /api/v1/auth/me succeeds with session cookie."""
    reg_payload = {
        "email": "cookie_user@example.com",
        "password": "Password123!",
        "full_name": "Cookie User",
        "tenant_name": "Cookie Org",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "cookie_user@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    me_res = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert me_res.status_code == status.HTTP_200_OK

    data = me_res.json()
    assert data["email"] == "cookie_user@example.com"
    assert data["full_name"] == "Cookie User"
    assert "user_id" in data
    assert "tenant_id" in data


def test_get_me_via_x_session_id_header(client):
    """Verify GET /api/v1/auth/me succeeds with X-Session-ID header."""
    reg_payload = {
        "email": "header_user@example.com",
        "password": "Password123!",
        "full_name": "Header User",
        "tenant_name": "Header Org",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "header_user@example.com", "password": "Password123!"},
    )
    session_id = login_res.headers["X-Session-ID"]

    me_res = client.get(
        "/api/v1/auth/me",
        headers={"X-Session-ID": session_id},
    )
    assert me_res.status_code == status.HTTP_200_OK
    assert me_res.json()["email"] == "header_user@example.com"


def test_get_me_via_bearer_token(client):
    """Verify GET /api/v1/auth/me succeeds with Authorization Bearer header."""
    reg_payload = {
        "email": "bearer_user@example.com",
        "password": "Password123!",
        "full_name": "Bearer User",
        "tenant_name": "Bearer Org",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "bearer_user@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    me_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {session_id}"},
    )
    assert me_res.status_code == status.HTTP_200_OK
    assert me_res.json()["email"] == "bearer_user@example.com"


def test_get_me_unauthenticated_missing_token(client):
    """Verify GET /api/v1/auth/me returns 401 Unauthorized when no authentication token is sent."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = response.json()
    assert err_data["detail"]["code"] == "UNAUTHORIZED"


def test_get_me_invalid_session_id(client):
    """Verify GET /api/v1/auth/me with invalid session ID returns 401 Unauthorized."""
    response = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: "invalid-uuid-session-token"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = response.json()
    assert err_data["detail"]["code"] == "UNAUTHORIZED"


def test_get_me_expired_session_integration(client, test_db):
    """Verify GET /api/v1/auth/me with expired session returns 401 and deletes session record."""
    reg_payload = {
        "email": "expired_session_user@example.com",
        "password": "Password123!",
        "full_name": "Expired Session User",
        "tenant_name": "Expired Org",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "expired_session_user@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    # Force session expiration in DB
    session_in_db = test_db.query(SessionModel).filter_by(id=session_id).first()
    assert session_in_db is not None
    session_in_db.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    test_db.commit()

    # Attempt /me with expired session
    response = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"

    # Verify expired session record was cleaned up in DB
    expired_session_check = test_db.query(SessionModel).filter_by(id=session_id).first()
    assert expired_session_check is None


def test_get_me_inactive_account_integration(client, test_db):
    """Verify GET /api/v1/auth/me for deactivated user account returns 401 Unauthorized."""
    reg_payload = {
        "email": "deactivated_user@example.com",
        "password": "Password123!",
        "full_name": "Deactivated User",
        "tenant_name": "Deactivated Org",
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    user_id = reg_res.json()["user_id"]

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "deactivated_user@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    # Deactivate user in DB after creating session
    user = test_db.query(UserModel).filter_by(id=user_id).first()
    user.is_active = False
    test_db.commit()

    response = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


# --- User Logout Integration Tests ---


def test_logout_success_integration(client, test_db):
    """Verify POST /api/v1/auth/logout invalidates session in DB and clears cookie."""
    reg_payload = {
        "email": "logout_user@example.com",
        "password": "Password123!",
        "full_name": "Logout User",
        "tenant_name": "Logout Org",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "logout_user@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    # Verify session exists in DB before logout
    assert test_db.query(SessionModel).filter_by(id=session_id).first() is not None

    logout_res = client.post(
        "/api/v1/auth/logout",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert logout_res.status_code == status.HTTP_200_OK
    assert logout_res.json()["status"] == "logged_out"

    # Verify session record deleted from DB
    assert test_db.query(SessionModel).filter_by(id=session_id).first() is None

    # Verify subsequent request to /me fails
    me_res = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert me_res.status_code == status.HTTP_401_UNAUTHORIZED


def test_logout_unauthenticated_integration(client):
    """Verify POST /api/v1/auth/logout returns 401 Unauthorized when unauthenticated."""
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


# --- Full Authentication End-to-End Workflow Test ---


def test_full_auth_lifecycle_integration(client, test_db):
    """Verify complete registration -> login -> profile access -> logout lifecycle."""
    # 1. Register
    reg_payload = {
        "email": "lifecycle_user@example.com",
        "password": "Password123!",
        "full_name": "Lifecycle User",
        "tenant_name": "Lifecycle Corp",
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == status.HTTP_201_CREATED
    user_id = reg_res.json()["user_id"]
    tenant_id = reg_res.json()["tenant_id"]

    # 2. Login
    login_payload = {
        "email": "lifecycle_user@example.com",
        "password": "Password123!",
    }
    login_res = client.post("/api/v1/auth/login", json=login_payload)
    assert login_res.status_code == status.HTTP_200_OK
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    # 3. Access profile (/me)
    me_res = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert me_res.status_code == status.HTTP_200_OK
    me_data = me_res.json()
    assert me_data["user_id"] == user_id
    assert me_data["tenant_id"] == tenant_id
    assert me_data["email"] == "lifecycle_user@example.com"

    # 4. Logout
    logout_res = client.post(
        "/api/v1/auth/logout",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert logout_res.status_code == status.HTTP_200_OK

    # 5. Profile access post-logout must fail
    me_after_logout = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert me_after_logout.status_code == status.HTTP_401_UNAUTHORIZED


# --- Tenant Isolation Integration Test ---


def test_tenant_isolation_validation_integration(client, test_db):
    """Verify tenant access validation logic enforces strict tenant context matching."""
    # Register user 1 (Tenant A)
    res_a = client.post(
        "/api/v1/auth/register",
        json={
            "email": "usera@tenant-a.com",
            "password": "Password123!",
            "full_name": "User A",
            "tenant_name": "Tenant A",
        },
    )
    tenant_a_id = res_a.json()["tenant_id"]

    # Register user 2 (Tenant B)
    res_b = client.post(
        "/api/v1/auth/register",
        json={
            "email": "userb@tenant-b.com",
            "password": "Password123!",
            "full_name": "User B",
            "tenant_name": "Tenant B",
        },
    )
    tenant_b_id = res_b.json()["tenant_id"]

    assert tenant_a_id != tenant_b_id

    # Login User A
    login_a = client.post(
        "/api/v1/auth/login",
        json={"email": "usera@tenant-a.com", "password": "Password123!"},
    )
    session_a_id = login_a.cookies[settings.SESSION_COOKIE_NAME]

    # Retrieve session entity from DB
    session_a = get_active_session(test_db, session_a_id)

    # Valid tenant access check for Tenant A
    assert validate_tenant_access(session_a, tenant_a_id) is True

    # Invalid tenant access check for Tenant B (must raise TenantMismatchError)
    with pytest.raises(TenantMismatchError):
        validate_tenant_access(session_a, tenant_b_id)
