"""Unit tests for Session ORM entity model."""

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session as DBSession

from src.models.base import Base, create_db_engine
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant
from src.models.user import User


def test_session_model_fields_and_persistence():
    """Verify Session entity creation, default UUID, timestamps, FKs, and DB roundtrip."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with DBSession(test_engine) as db_session:
        tenant = Tenant(name="Acme Corp")
        db_session.add(tenant)
        db_session.commit()

        user = User(
            tenant_id=tenant.id,
            email="session_user@acme.com",
            password_hash="hashed_secret",
            full_name="Session User",
        )
        db_session.add(user)
        db_session.commit()

        expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        session_record = SessionModel(
            user_id=user.id,
            tenant_id=tenant.id,
            expires_at=expiry,
        )
        db_session.add(session_record)
        db_session.commit()

        # Retrieve persisted session entity
        retrieved = (
            db_session.query(SessionModel)
            .filter_by(id=session_record.id)
            .first()
        )
        assert retrieved is not None
        assert retrieved.id == session_record.id
        assert retrieved.user_id == user.id
        assert retrieved.tenant_id == tenant.id

        # Verify valid UUID format
        parsed_uuid = uuid.UUID(retrieved.id)
        assert str(parsed_uuid) == retrieved.id

        assert isinstance(retrieved.created_at, datetime)
        assert isinstance(retrieved.expires_at, datetime)

        # Verify relationships
        assert retrieved.user is not None
        assert retrieved.user.id == user.id
        assert retrieved.user.email == "session_user@acme.com"

        assert retrieved.tenant is not None
        assert retrieved.tenant.id == tenant.id
        assert retrieved.tenant.name == "Acme Corp"


def test_session_model_explicit_id():
    """Verify Session model accepts an explicitly supplied UUID id."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    explicit_session_id = str(uuid.uuid4())
    with DBSession(test_engine) as db_session:
        tenant = Tenant(name="Globex")
        db_session.add(tenant)
        db_session.commit()

        user = User(
            tenant_id=tenant.id,
            email="globex_user@globex.com",
            password_hash="hashed_secret_globex",
            full_name="Globex User",
        )
        db_session.add(user)
        db_session.commit()

        expiry = datetime.now(timezone.utc) + timedelta(days=7)
        session_record = SessionModel(
            id=explicit_session_id,
            user_id=user.id,
            tenant_id=tenant.id,
            expires_at=expiry,
        )
        db_session.add(session_record)
        db_session.commit()

        retrieved = (
            db_session.query(SessionModel)
            .filter_by(id=explicit_session_id)
            .first()
        )
        assert retrieved is not None
        assert retrieved.id == explicit_session_id
        assert retrieved.user_id == user.id
