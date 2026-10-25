"""Unit tests for authentication API endpoints in backend/src/api/routes/auth.py."""

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.routes.auth import router as auth_router
from src.lib.config import settings
from src.models.base import Base, create_db_engine, get_db


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
    app = FastAPI()
    app.include_router(auth_router)

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_register_user_success(client):
    """Verify user registration endpoint returns 201 Created and user profile data."""
    payload = {
        "email": "newuser@example.com",
        "password": "Password123!",
        "full_name": "New User",
        "tenant_name": "Acme Corp",
    }
    res = client.post("/api/v1/auth/register", json=payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["email"] == "newuser@example.com"
    assert data["full_name"] == "New User"
    assert "user_id" in data
    assert "tenant_id" in data


def test_register_user_duplicate_email_conflict(client):
    """Verify registration with duplicate email returns 409 Conflict ACCOUNT_EXISTS."""
    payload = {
        "email": "dup@example.com",
        "password": "Password123!",
        "full_name": "First User",
        "tenant_name": "Acme Corp",
    }
    res1 = client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == status.HTTP_409_CONFLICT
    err_data = res2.json()
    assert err_data["detail"]["code"] == "ACCOUNT_EXISTS"


def test_register_user_invalid_input(client):
    """Verify registration with empty/invalid inputs returns 400 Bad Request."""
    payload = {
        "email": "  ",
        "password": "Password123!",
        "full_name": "Blank Email",
        "tenant_name": "Acme Corp",
    }
    res = client.post("/api/v1/auth/register", json=payload)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    err_data = res.json()
    assert err_data["detail"]["code"] == "INVALID_INPUT"


def test_login_success(client):
    """Verify login with valid credentials sets session cookie and header and returns user summary."""
    reg_payload = {
        "email": "login_test@example.com",
        "password": "SecretPassword123!",
        "full_name": "Login User",
        "tenant_name": "Login Tenant",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_payload = {
        "email": "login_test@example.com",
        "password": "SecretPassword123!",
    }
    res = client.post("/api/v1/auth/login", json=login_payload)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["status"] == "success"
    assert data["user"]["email"] == "login_test@example.com"
    assert data["user"]["full_name"] == "Login User"

    # Verify session cookie and header
    assert settings.SESSION_COOKIE_NAME in res.cookies
    assert "X-Session-ID" in res.headers


def test_login_invalid_credentials(client):
    """Verify login with wrong password returns 401 Unauthorized INVALID_CREDENTIALS."""
    reg_payload = {
        "email": "wrong_pass@example.com",
        "password": "SecretPassword123!",
        "full_name": "Wrong Pass User",
        "tenant_name": "Tenant",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_payload = {
        "email": "wrong_pass@example.com",
        "password": "WrongPassword!",
    }
    res = client.post("/api/v1/auth/login", json=login_payload)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = res.json()
    assert err_data["detail"]["code"] == "INVALID_CREDENTIALS"


def test_get_current_user_me_authenticated(client):
    """Verify /me endpoint returns profile when authenticated with session cookie."""
    reg_payload = {
        "email": "me_test@example.com",
        "password": "Password123!",
        "full_name": "Me User",
        "tenant_name": "Me Tenant",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "me_test@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    me_res = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert me_res.status_code == status.HTTP_200_OK
    data = me_res.json()
    assert data["email"] == "me_test@example.com"
    assert data["full_name"] == "Me User"
    assert "user_id" in data
    assert "tenant_id" in data


def test_get_current_user_me_unauthenticated(client):
    """Verify /me endpoint returns 401 Unauthorized when no session token is provided."""
    me_res = client.get("/api/v1/auth/me")
    assert me_res.status_code == status.HTTP_401_UNAUTHORIZED
    err_data = me_res.json()
    assert err_data["detail"]["code"] == "UNAUTHORIZED"


def test_logout_success(client):
    """Verify logout terminates active session and clears session cookie."""
    reg_payload = {
        "email": "logout_test@example.com",
        "password": "Password123!",
        "full_name": "Logout User",
        "tenant_name": "Logout Tenant",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "logout_test@example.com", "password": "Password123!"},
    )
    session_id = login_res.cookies[settings.SESSION_COOKIE_NAME]

    logout_res = client.post(
        "/api/v1/auth/logout",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert logout_res.status_code == status.HTTP_200_OK
    assert logout_res.json()["status"] == "logged_out"

    # Subsequent /me request should now fail with 401
    me_res = client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_id},
    )
    assert me_res.status_code == status.HTTP_401_UNAUTHORIZED


def test_logout_unauthenticated(client):
    """Verify logout endpoint returns 401 Unauthorized when unauthenticated."""
    logout_res = client.post("/api/v1/auth/logout")
    assert logout_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert logout_res.json()["detail"]["code"] == "UNAUTHORIZED"
