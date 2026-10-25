"""Unit tests for Lead and LeadSource ORM entity models."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.lead import Lead, LeadSource


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_lead_source_creation_and_persistence(test_session: Session):
    """Verify LeadSource creation, attribute types, UUID generation, and retrieval."""
    source = LeadSource(
        source_name="Organic Search",
        cost_amount=Decimal("1250.00"),
    )
    test_session.add(source)
    test_session.commit()

    retrieved = test_session.query(LeadSource).filter_by(source_name="Organic Search").first()
    assert retrieved is not None
    assert retrieved.source_id is not None
    assert retrieved.id == retrieved.source_id
    assert str(uuid.UUID(retrieved.source_id)) == retrieved.source_id
    assert retrieved.source_name == "Organic Search"
    assert retrieved.cost_amount == Decimal("1250.00")
    assert isinstance(retrieved.created_timestamp, datetime)


def test_lead_source_unique_name_constraint(test_session: Session):
    """Verify duplicate lead source names trigger an IntegrityError."""
    source1 = LeadSource(source_name="Paid Ads", cost_amount=Decimal("500.00"))
    source2 = LeadSource(source_name="Paid Ads", cost_amount=Decimal("750.00"))
    test_session.add(source1)
    test_session.commit()

    test_session.add(source2)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()


def test_lead_creation_and_persistence(test_session: Session):
    """Verify Lead creation, relationship to LeadSource, attribute types, and region mapping."""
    source = LeadSource(source_name="Email Campaign", cost_amount=Decimal("300.00"))
    test_session.add(source)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=85,
        status_stage="QUALIFIED",
        state_code="CA",
        postal_code="94105",
        region_name="West",
        deal_value=Decimal("25000.00"),
    )
    test_session.add(lead)
    test_session.commit()

    retrieved = test_session.query(Lead).filter_by(lead_id=lead.lead_id).first()
    assert retrieved is not None
    assert retrieved.lead_id is not None
    assert retrieved.id == retrieved.lead_id
    assert str(uuid.UUID(retrieved.lead_id)) == retrieved.lead_id
    assert retrieved.source_id == source.source_id
    assert retrieved.quality_score == 85
    assert retrieved.status_stage == "QUALIFIED"
    assert retrieved.state_code == "CA"
    assert retrieved.postal_code == "94105"
    assert retrieved.region_name == "West"
    assert retrieved.deal_value == Decimal("25000.00")
    assert isinstance(retrieved.created_timestamp, datetime)
    assert retrieved.source.source_name == "Email Campaign"
    assert len(source.leads) == 1
    assert source.leads[0].lead_id == lead.lead_id


def test_lead_unmapped_region_default(test_session: Session):
    """Verify leads missing region_name automatically default to 'Unmapped Region'."""
    source = LeadSource(source_name="Referral", cost_amount=Decimal("0.00"))
    test_session.add(source)
    test_session.commit()

    # Create lead without explicit region_name
    lead_no_region = Lead(
        source_id=source.source_id,
        quality_score=60,
        deal_value=Decimal("5000.00"),
    )
    # Create lead with None as region_name
    lead_none_region = Lead(
        source_id=source.source_id,
        quality_score=40,
        region_name=None,
        deal_value=Decimal("2000.00"),
    )
    # Create lead with empty string as region_name
    lead_empty_region = Lead(
        source_id=source.source_id,
        quality_score=30,
        region_name="",
        deal_value=Decimal("1000.00"),
    )

    test_session.add_all([lead_no_region, lead_none_region, lead_empty_region])
    test_session.commit()

    for l in [lead_no_region, lead_none_region, lead_empty_region]:
        retrieved = test_session.query(Lead).filter_by(lead_id=l.lead_id).first()
        assert retrieved is not None
        assert retrieved.region_name == "Unmapped Region"


def test_lead_source_cascade_delete(test_session: Session):
    """Verify deleting a LeadSource deletes associated leads due to cascade orphan removal."""
    source = LeadSource(source_name="Event Webinar", cost_amount=Decimal("1000.00"))
    test_session.add(source)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=90,
        region_name="EMEA",
        deal_value=Decimal("45000.00"),
    )
    test_session.add(lead)
    test_session.commit()

    test_session.delete(source)
    test_session.commit()

    assert test_session.query(LeadSource).filter_by(source_id=source.source_id).first() is None
    assert test_session.query(Lead).filter_by(lead_id=lead.lead_id).first() is None
