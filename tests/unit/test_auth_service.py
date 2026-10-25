"""Unit tests for authentication service domain logic and session lifecycle."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session as DBSession

from src.models.base import Base, create_db_engine
from src.models.session import Session
from src.models.tenant import Tenant
from src.models.user import User
from src.services.auth.crypto import hash_password
from src.services.auth.service import (
    AccountExistsError,
    AuthService,
    InvalidCredentialsError,
    TenantMismatchError,
    UnauthorizedError,
    authenticate_user,
    create_session,
    get_active_session,
    register_user,
    terminate_session,
    validate_tenant_access,
)


@pytest.fixture
def test_db():
    """Fixture providing an isolated in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


def test_register_user_success(test_db):
    """Verify successful user registration and tenant creation."""
    user = register_user(
        db=test_db,
        email="alice@acme.com",
        password="securePassword123!",
        full_name="Alice Smith",
        tenant_name="Acme Corp",
    )

    assert user is not None
    assert user.id is not None
    assert user.email == "alice@acme.com"
    assert user.full_name == "Alice Smith"
    assert user.is_active is True
    assert user.tenant is not None
    assert user.tenant.name == "Acme Corp"

    # Verify database persistence
    retrieved_user = test_db.query(User).filter_by(id=user.id).first()
    assert retrieved_user is not None
    assert retrieved_user.email == "alice@acme.com"


def test_register_user_existing_tenant_reuse(test_db):
    """Verify registering a second user under an existing tenant name reuses that tenant."""
    user1 = register_user(
        db=test_db,
        email="user1@globex.com",
        password="Password123!",
        full_name="User One",
        tenant_name="Globex Corp",
    )

    user2 = register_user(
        db=test_db,
        email="user2@globex.com",
        password="Password123!",
        full_name="User Two",
        tenant_name="Globex Corp",
    )

    assert user1.tenant_id == user2.tenant_id
    tenant_count = test_db.query(Tenant).filter(Tenant.name == "Globex Corp").count()
    assert tenant_count == 1


def test_register_user_duplicate_email_conflict(test_db):
    """Verify registration with duplicate email raises AccountExistsError."""
    register_user(
        db=test_db,
        email="duplicate@stark.com",
        password="Password123!",
        full_name="Tony Stark",
        tenant_name="Stark Industries",
    )

    with pytest.raises(AccountExistsError) as exc_info:
        register_user(
            db=test_db,
            email="DUPLICATE@stark.com",  # Case-insensitive duplicate check
            password="OtherPassword123!",
            full_name="Anthony Stark",
            tenant_name="Stark Industries",
        )

    assert "duplicate@stark.com" in str(exc_info.value)


def test_register_user_input_validation(test_db):
    """Verify invalid registration arguments raise ValueError."""
    with pytest.raises(ValueError):
        register_user(test_db, "", "pass", "Name", "Tenant")

    with pytest.raises(ValueError):
        register_user(test_db, "email@test.com", "", "Name", "Tenant")

    with pytest.raises(ValueError):
        register_user(test_db, "email@test.com", "pass", "", "Tenant")

    with pytest.raises(ValueError):
        register_user(test_db, "email@test.com", "pass", "Name", "  ")


def test_authenticate_user_success(test_db):
    """Verify successful authentication with valid credentials."""
    registered_user = register_user(
        db=test_db,
        email="auth_user@test.com",
        password="mySecretPassword",
        full_name="Auth User",
        tenant_name="Test Tenant",
    )

    authenticated_user = authenticate_user(
        db=test_db,
        email="AUTH_USER@test.com",  # Case insensitive
        password="mySecretPassword",
    )

    assert authenticated_user.id == registered_user.id
    assert authenticated_user.email == "auth_user@test.com"


def test_authenticate_user_invalid_password(test_db):
    """Verify authentication failure on wrong password raises InvalidCredentialsError."""
    register_user(
        db=test_db,
        email="user@wrongpass.com",
        password="correctPassword",
        full_name="User Name",
        tenant_name="Tenant Name",
    )

    with pytest.raises(InvalidCredentialsError):
        authenticate_user(
            db=test_db,
            email="user@wrongpass.com",
            password="wrongPassword",
        )


def test_authenticate_user_nonexistent_email(test_db):
    """Verify authentication with non-existent email raises InvalidCredentialsError."""
    with pytest.raises(InvalidCredentialsError):
        authenticate_user(
            db=test_db,
            email="nonexistent@domain.com",
            password="somePassword",
        )


def test_authenticate_user_inactive_account(test_db):
    """Verify authentication failure for deactivated accounts."""
    user = register_user(
        db=test_db,
        email="inactive@domain.com",
        password="password123",
        full_name="Inactive User",
        tenant_name="Domain Tenant",
    )
    user.is_active = False
    test_db.commit()

    with pytest.raises(InvalidCredentialsError):
        authenticate_user(
            db=test_db,
            email="inactive@domain.com",
            password="password123",
        )


def test_session_lifecycle(test_db):
    """Verify create, retrieve, and terminate session lifecycle."""
    user = register_user(
        db=test_db,
        email="session_test@domain.com",
        password="password123",
        full_name="Session Test",
        tenant_name="Session Tenant",
    )

    # 1. Create session
    session_record = create_session(
        db=test_db,
        user_id=user.id,
        tenant_id=user.tenant_id,
        expires_in_hours=12,
    )

    assert session_record is not None
    assert session_record.id is not None
    assert session_record.user_id == user.id
    assert session_record.tenant_id == user.tenant_id

    # 2. Get active session
    active_session = get_active_session(
        db=test_db,
        session_id=session_record.id,
    )
    assert active_session.id == session_record.id
    assert active_session.user.email == "session_test@domain.com"

    # 3. Terminate session
    success = terminate_session(
        db=test_db,
        session_id=session_record.id,
    )
    assert success is True

    # 4. Verify terminated session is no longer accessible
    with pytest.raises(UnauthorizedError):
        get_active_session(
            db=test_db,
            session_id=session_record.id,
        )


def test_get_active_session_expired(test_db):
    """Verify getting an expired session raises UnauthorizedError and cleans it up."""
    tenant = Tenant(name="Expired Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = User(
        tenant_id=tenant.id,
        email="expired@test.com",
        password_hash=hash_password("pwd"),
        full_name="Expired User",
    )
    test_db.add(user)
    test_db.flush()

    # Expired 1 hour ago
    past_expiration = datetime.now(timezone.utc) - timedelta(hours=1)
    expired_session = Session(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=past_expiration,
    )
    test_db.add(expired_session)
    test_db.commit()

    session_id = expired_session.id

    with pytest.raises(UnauthorizedError) as exc_info:
        get_active_session(test_db, session_id)

    assert "expired" in str(exc_info.value).lower()

    # Verify session record was deleted from DB
    persisted = test_db.query(Session).filter_by(id=session_id).first()
    assert persisted is None


def test_tenant_access_validation(test_db):
    """Verify tenant context matching and isolation enforcement."""
    user = register_user(
        db=test_db,
        email="tenant_val@test.com",
        password="password123",
        full_name="Tenant User",
        tenant_name="Tenant Alpha",
    )

    session_record = create_session(
        db=test_db,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )

    # Same tenant match
    assert validate_tenant_access(session_record, user.tenant_id) is True

    # Mismatched tenant raises TenantMismatchError
    with pytest.raises(TenantMismatchError):
        validate_tenant_access(session_record, "other-tenant-uuid-1234")


def test_auth_service_class_wrapper(test_db):
    """Verify static methods on AuthService class wrapper."""
    user = AuthService.register_user(
        db=test_db,
        email="wrapper@test.com",
        password="pass",
        full_name="Wrapper User",
        tenant_name="Wrapper Tenant",
    )
    assert user.email == "wrapper@test.com"

    authed = AuthService.authenticate_user(test_db, "wrapper@test.com", "pass")
    assert authed.id == user.id

    sess = AuthService.create_session(test_db, authed.id, authed.tenant_id)
    active = AuthService.get_active_session(test_db, sess.id)
    assert active.id == sess.id

    AuthService.validate_tenant_access(active, authed.tenant_id)

    AuthService.terminate_session(test_db, sess.id)
