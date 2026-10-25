"""Authentication API endpoint routes for user registration, login, logout, and current user info."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session, get_current_user
from src.lib.config import settings
from src.models.base import get_db
from src.models.session import Session as SessionModel
from src.models.user import User as UserModel
from src.services.auth.service import (
    AccountExistsError,
    AuthService,
    AuthServiceError,
    InvalidCredentialsError,
    UnauthorizedError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# --- Request and Response Schemas ---


class RegisterRequest(BaseModel):
    """Payload for user account registration."""

    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User plaintext password")
    full_name: str = Field(..., description="User full display name")
    tenant_name: str = Field(..., description="Organization/Tenant name")


class RegisterResponse(BaseModel):
    """Response returned upon successful user registration."""

    user_id: str
    email: str
    tenant_id: str
    full_name: str


class LoginRequest(BaseModel):
    """Payload for user login authentication."""

    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class UserSummary(BaseModel):
    """Summary of authenticated user account."""

    id: str
    email: str
    full_name: str
    tenant_id: str


class LoginResponse(BaseModel):
    """Response returned upon successful login authentication."""

    status: str = "success"
    user: UserSummary


class LogoutResponse(BaseModel):
    """Response returned upon session termination."""

    status: str = "logged_out"


class CurrentUserResponse(BaseModel):
    """Response containing currently authenticated user details."""

    user_id: str
    email: str
    full_name: str
    tenant_id: str


# --- Endpoint Handlers ---


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    summary="Register new user account and tenant",
)
def register_user_endpoint(
    payload: RegisterRequest,
    db: DBSession = Depends(get_db),
) -> RegisterResponse:
    """Register a new user account bound to a new or existing tenant organization.

    Args:
        payload: Registration payload containing email, password, full name, and tenant name.
        db: Synchronous database session.

    Returns:
        RegisterResponse with created user details.

    Raises:
        HTTPException 409: If an account with the given email already exists.
        HTTPException 400: If registration input is invalid or missing.
    """
    try:
        user = AuthService.register_user(
            db=db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            tenant_name=payload.tenant_name,
        )
        return RegisterResponse(
            user_id=user.id,
            email=user.email,
            tenant_id=user.tenant_id,
            full_name=user.full_name,
        )
    except AccountExistsError as err:
        logger.warning("Registration rejected: account exists for email %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ACCOUNT_EXISTS",
                "message": str(err),
                "details": {},
            },
        )
    except ValueError as err:
        logger.warning("Registration rejected: invalid input payload - %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_INPUT",
                "message": str(err),
                "details": {},
            },
        )
    except AuthServiceError as err:
        logger.error("Registration failed due to auth service error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "REGISTRATION_FAILED",
                "message": str(err),
                "details": {},
            },
        )


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=LoginResponse,
    summary="Authenticate user and issue session token",
)
def login_endpoint(
    payload: LoginRequest,
    response: Response,
    db: DBSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate user credentials and create a server-managed session.

    Sets an HTTP-only session cookie and X-Session-ID header on success.

    Args:
        payload: Login credentials (email and password).
        response: FastAPI Response object used for setting cookies/headers.
        db: Synchronous database session.

    Returns:
        LoginResponse with authenticated user summary.

    Raises:
        HTTPException 401: If email or password is invalid or account is inactive.
    """
    try:
        user = AuthService.authenticate_user(
            db=db,
            email=payload.email,
            password=payload.password,
        )
        session = AuthService.create_session(
            db=db,
            user_id=user.id,
            tenant_id=user.tenant_id,
            expires_in_hours=settings.SESSION_EXPIRE_HOURS,
        )

        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=session.id,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        )
        response.headers["X-Session-ID"] = session.id

        return LoginResponse(
            status="success",
            user=UserSummary(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                tenant_id=user.tenant_id,
            ),
        )
    except InvalidCredentialsError as err:
        logger.warning("Login authentication failed for email %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": str(err),
                "details": {},
            },
        )
    except UnauthorizedError as err:
        logger.warning("Login authentication unauthorized: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": str(err),
                "details": {},
            },
        )
    except AuthServiceError as err:
        logger.error("Login failed due to auth service error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTHENTICATION_FAILED",
                "message": str(err),
                "details": {},
            },
        )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    response_model=LogoutResponse,
    summary="Terminate active user session and clear cookie",
)
def logout_endpoint(
    response: Response,
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> LogoutResponse:
    """Terminate the active session in the database and clear the session cookie.

    Args:
        response: FastAPI Response object for clearing cookies.
        session: Active session extracted via request dependency.
        db: Synchronous database session.

    Returns:
        LogoutResponse confirming session clearance.

    Raises:
        HTTPException 401: If active session is missing or invalid.
    """
    try:
        AuthService.terminate_session(db=db, session_id=session.id)
    except UnauthorizedError as err:
        logger.warning("Logout termination failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": str(err),
                "details": {},
            },
        )

    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
    )
    return LogoutResponse(status="logged_out")


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=CurrentUserResponse,
    summary="Retrieve current authenticated user context",
)
def get_current_user_endpoint(
    user: UserModel = Depends(get_current_user),
) -> CurrentUserResponse:
    """Get profile and tenant context details for the current authenticated user.

    Args:
        user: Authenticated User entity from request dependency.

    Returns:
        CurrentUserResponse with profile information.

    Raises:
        HTTPException 401: If unauthenticated or user account is missing/inactive.
    """
    return CurrentUserResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        tenant_id=user.tenant_id,
    )
