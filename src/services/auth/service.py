"""Authentication domain service handling user registration, login, session lifecycle, and tenant binding."""

import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session as DBSession

from src.models.session import Session
from src.models.tenant import Tenant
from src.models.user import User
from src.services.auth.crypto import hash_password, needs_rehash, verify_password

logger = logging.getLogger(__name__)


class AuthServiceError(Exception):
    """Base exception class for authentication service errors."""

    pass


class AccountExistsError(AuthServiceError):
    """Raised when user registration is attempted with an already registered email."""

    pass


class InvalidCredentialsError(AuthServiceError):
    """Raised when authentication credentials (email or password) are invalid."""

    pass


class UnauthorizedError(AuthServiceError):
    """Raised when a session token is missing, invalid, expired, or deactivated."""

    pass


class TenantMismatchError(AuthServiceError):
    """Raised when a session tenant context does not match target entity tenant context."""

    pass


def register_user(
    db: DBSession,
    email: str,
    password: str,
    full_name: str,
    tenant_name: str,
) -> User:
    """Register a new user and bind them to a tenant.

    Creates a new tenant if one with the given name does not already exist.

    Args:
        db: Synchronous SQLAlchemy database session.
        email: User email address.
        password: Plaintext password string.
        full_name: Full display name of the user.
        tenant_name: Name of the tenant organization.

    Returns:
        Persisted User ORM entity.

    Raises:
        ValueError: If required arguments are empty or invalid.
        AccountExistsError: If email is already registered.
    """
    if not email or not isinstance(email, str) or not email.strip():
        raise ValueError("Email address must be a non-empty string.")
    if not password or not isinstance(password, str):
        raise ValueError("Password must be a non-empty string.")
    if not full_name or not isinstance(full_name, str) or not full_name.strip():
        raise ValueError("Full name must be a non-empty string.")
    if not tenant_name or not isinstance(tenant_name, str) or not tenant_name.strip():
        raise ValueError("Tenant name must be a non-empty string.")

    normalized_email = email.strip().lower()
    clean_full_name = full_name.strip()
    clean_tenant_name = tenant_name.strip()

    # Check for existing user account by email
    existing_user = (
        db.query(User).filter(User.email == normalized_email).first()
    )
    if existing_user is not None:
        logger.warning(
            "Registration attempt failed: account exists for email %s",
            normalized_email,
        )
        raise AccountExistsError(f"Account with email '{normalized_email}' already exists.")

    # Find or create tenant entity
    tenant = (
        db.query(Tenant).filter(Tenant.name == clean_tenant_name).first()
    )
    if tenant is None:
        tenant = Tenant(name=clean_tenant_name)
        db.add(tenant)
        db.flush()
        logger.info("Created new tenant '%s' with id %s", clean_tenant_name, tenant.id)

    # Hash password and persist user
    hashed_password = hash_password(password)
    user = User(
        tenant_id=tenant.id,
        email=normalized_email,
        password_hash=hashed_password,
        full_name=clean_full_name,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(
        "Successfully registered user %s (id: %s) under tenant %s",
        normalized_email,
        user.id,
        tenant.id,
    )
    return user


def authenticate_user(
    db: DBSession,
    email: str,
    password: str,
) -> User:
    """Authenticate user credentials against stored password hash.

    Args:
        db: Synchronous SQLAlchemy database session.
        email: User email address.
        password: Plaintext password to verify.

    Returns:
        Authenticated User ORM entity.

    Raises:
        InvalidCredentialsError: If email is not found, inactive, or password fails.
    """
    if not email or not isinstance(email, str) or not password or not isinstance(password, str):
        raise InvalidCredentialsError("Invalid email or password.")

    normalized_email = email.strip().lower()
    user = db.query(User).filter(User.email == normalized_email).first()

    if user is None or not user.is_active:
        logger.warning("Authentication failed for email %s: user not found or inactive", normalized_email)
        raise InvalidCredentialsError("Invalid email or password.")

    if not verify_password(password, user.password_hash):
        logger.warning("Authentication failed for email %s: password mismatch", normalized_email)
        raise InvalidCredentialsError("Invalid email or password.")

    # Check if hash needs update to primary algorithm scheme
    if needs_rehash(user.password_hash):
        logger.info("Rehashing password for user %s to primary scheme", user.id)
        user.password_hash = hash_password(password)
        db.commit()
        db.refresh(user)

    logger.info("Successfully authenticated user %s", user.id)
    return user


def create_session(
    db: DBSession,
    user_id: str,
    tenant_id: str,
    expires_in_hours: int = 24,
) -> Session:
    """Create a new server-managed session for an authenticated user bound to a tenant.

    Args:
        db: Synchronous SQLAlchemy database session.
        user_id: Identifier of the authenticated user.
        tenant_id: Identifier of the active tenant context.
        expires_in_hours: Expiration period in hours (default: 24).

    Returns:
        Persisted Session ORM entity.

    Raises:
        UnauthorizedError: If user is inactive or not found for tenant.
    """
    user = (
        db.query(User)
        .filter(User.id == user_id, User.tenant_id == tenant_id)
        .first()
    )
    if user is None or not user.is_active:
        logger.warning("Session creation failed: user %s not found or inactive for tenant %s", user_id, tenant_id)
        raise UnauthorizedError("User is invalid, inactive, or unbound to tenant.")

    expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
    session_record = Session(
        user_id=user_id,
        tenant_id=tenant_id,
        expires_at=expires_at,
    )
    db.add(session_record)
    db.commit()
    db.refresh(session_record)

    logger.info("Created session %s for user %s (tenant %s)", session_record.id, user_id, tenant_id)
    return session_record


def get_active_session(
    db: DBSession,
    session_id: str,
) -> Session:
    """Retrieve and validate an active session by session ID token.

    Args:
        db: Synchronous SQLAlchemy database session.
        session_id: UUID session token string.

    Returns:
        Active Session ORM entity with loaded relationship models.

    Raises:
        UnauthorizedError: If session is missing, expired, or associated user is inactive.
    """
    if not session_id or not isinstance(session_id, str):
        raise UnauthorizedError("Session ID token is missing or invalid.")

    session_record = (
        db.query(Session)
        .filter(Session.id == session_id)
        .first()
    )
    if session_record is None:
        logger.warning("Session lookup failed: token %s not found", session_id)
        raise UnauthorizedError("Session is invalid or does not exist.")

    now = datetime.now(timezone.utc)
    expires_at = session_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        logger.info("Session %s has expired (expires_at: %s)", session_id, expires_at)
        db.delete(session_record)
        db.commit()
        raise UnauthorizedError("Session token has expired.")

    if session_record.user is None or not session_record.user.is_active:
        logger.warning("Session %s user account is inactive or missing", session_id)
        raise UnauthorizedError("User account associated with session is inactive.")

    return session_record


def terminate_session(
    db: DBSession,
    session_id: str,
) -> bool:
    """Terminate and invalidate an active user session.

    Args:
        db: Synchronous SQLAlchemy database session.
        session_id: UUID session token string.

    Returns:
        True if session was terminated successfully.

    Raises:
        UnauthorizedError: If session ID does not exist.
    """
    if not session_id or not isinstance(session_id, str):
        raise UnauthorizedError("Session ID token is missing or invalid.")

    session_record = (
        db.query(Session)
        .filter(Session.id == session_id)
        .first()
    )
    if session_record is None:
        logger.warning("Logout attempt failed: session token %s not found", session_id)
        raise UnauthorizedError("Session is invalid or does not exist.")

    db.delete(session_record)
    db.commit()
    logger.info("Successfully terminated session %s", session_id)
    return True


def validate_tenant_access(
    session: Session,
    target_tenant_id: str,
) -> bool:
    """Enforce strict server-side tenant isolation binding.

    Args:
        session: Active user session record.
        target_tenant_id: Target resource or entity tenant ID.

    Returns:
        True if tenant context matches.

    Raises:
        TenantMismatchError: If active session tenant does not match target tenant ID.
    """
    if not session or not session.tenant_id:
        raise TenantMismatchError("Active session does not contain valid tenant context.")

    if session.tenant_id != target_tenant_id:
        logger.warning(
            "Tenant access violation: session tenant %s attempted access to target tenant %s",
            session.tenant_id,
            target_tenant_id,
        )
        raise TenantMismatchError("Tenant context mismatch.")

    return True


class AuthService:
    """Class wrapper providing static access to authentication service workflows."""

    register_user = staticmethod(register_user)
    authenticate_user = staticmethod(authenticate_user)
    create_session = staticmethod(create_session)
    get_active_session = staticmethod(get_active_session)
    terminate_session = staticmethod(terminate_session)
    validate_tenant_access = staticmethod(validate_tenant_access)
