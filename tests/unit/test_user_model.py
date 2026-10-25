"""Unit tests for User ORM entity model."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.tenant import Tenant
from src.models.user import User


def test_user_model_fields_and_persistence():
    """Verify User entity creation, default UUID, timestamps, FK to Tenant, and DB roundtrip."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with Session(test_engine) as session:
        tenant = Tenant(name="Acme Corp")
        session.add(tenant)
        session.commit()

        user_email = "alice@acme.com"
        user = User(
            tenant_id=tenant.id,
            email=user_email,
            password_hash="hashed_secret_123",
            full_name="Alice Smith",
        )
        session.add(user)
        session.commit()

        # Retrieve persisted user entity
        retrieved = session.query(User).filter_by(email=user_email).first()
        assert retrieved is not None
        assert retrieved.email == user_email
        assert retrieved.tenant_id == tenant.id
        assert retrieved.password_hash == "hashed_secret_123"
        assert retrieved.full_name == "Alice Smith"
        assert retrieved.is_active is True
        assert retrieved.id is not None

        # Verify valid UUID format
        parsed_uuid = uuid.UUID(retrieved.id)
        assert str(parsed_uuid) == retrieved.id

        assert isinstance(retrieved.created_at, datetime)
        assert isinstance(retrieved.updated_at, datetime)

        # Verify relationship to Tenant
        assert retrieved.tenant is not None
        assert retrieved.tenant.id == tenant.id
        assert retrieved.tenant.name == "Acme Corp"


def test_user_model_explicit_id():
    """Verify User model accepts an explicitly supplied UUID id."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    explicit_user_id = str(uuid.uuid4())
    with Session(test_engine) as session:
        tenant = Tenant(name="Globex")
        session.add(tenant)
        session.commit()

        user = User(
            id=explicit_user_id,
            tenant_id=tenant.id,
            email="bob@globex.com",
            password_hash="hashed_secret_456",
            full_name="Bob Jones",
        )
        session.add(user)
        session.commit()

        retrieved = session.query(User).filter_by(id=explicit_user_id).first()
        assert retrieved is not None
        assert retrieved.id == explicit_user_id
        assert retrieved.email == "bob@globex.com"


def test_user_model_unique_email_constraint():
    """Verify unique constraint on email field raises IntegrityError on duplicates."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with Session(test_engine) as session:
        tenant = Tenant(name="Stark Tech")
        session.add(tenant)
        session.commit()

        user1 = User(
            tenant_id=tenant.id,
            email="duplicate@stark.com",
            password_hash="hash1",
            full_name="User One",
        )
        session.add(user1)
        session.commit()

        user2 = User(
            tenant_id=tenant.id,
            email="duplicate@stark.com",
            password_hash="hash2",
            full_name="User Two",
        )
        session.add(user2)
        with pytest.raises(IntegrityError):
            session.commit()
