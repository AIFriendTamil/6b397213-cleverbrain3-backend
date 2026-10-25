"""Unit tests for Tenant ORM entity model."""

import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from src.models.base import Base, create_db_engine
from src.models.tenant import Tenant


def test_tenant_model_fields_and_persistence():
    """Verify Tenant entity creation, default UUID, timestamps, and database roundtrip."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    with Session(test_engine) as session:
        tenant_name = "Acme Corp"
        tenant = Tenant(name=tenant_name)
        session.add(tenant)
        session.commit()

        # Retrieve persisted entity
        retrieved = session.query(Tenant).filter_by(name=tenant_name).first()
        assert retrieved is not None
        assert retrieved.name == tenant_name
        assert retrieved.id is not None
        # Verify valid UUID format
        parsed_uuid = uuid.UUID(retrieved.id)
        assert str(parsed_uuid) == retrieved.id

        assert isinstance(retrieved.created_at, datetime)
        assert isinstance(retrieved.updated_at, datetime)


def test_tenant_model_explicit_id():
    """Verify Tenant model accepts an explicitly supplied UUID id."""
    test_engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)

    explicit_id = str(uuid.uuid4())
    with Session(test_engine) as session:
        tenant = Tenant(id=explicit_id, name="Globex")
        session.add(tenant)
        session.commit()

        retrieved = session.query(Tenant).filter_by(id=explicit_id).first()
        assert retrieved is not None
        assert retrieved.id == explicit_id
        assert retrieved.name == "Globex"
