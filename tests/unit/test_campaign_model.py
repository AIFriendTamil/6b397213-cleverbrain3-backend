"""Unit tests for Campaign ORM entity model."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.campaign import Campaign
from src.models.lead import Lead, LeadSource


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_campaign_creation_and_persistence(test_session: Session):
    """Verify Campaign creation, attributes, timestamps, budget fields, and persistence."""
    start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)

    campaign = Campaign(
        campaign_name="Q1 Product Launch",
        budget_amount=Decimal("50000.00"),
        spent_amount=Decimal("12500.50"),
        start_timestamp=start_time,
        end_timestamp=end_time,
    )
    test_session.add(campaign)
    test_session.commit()

    retrieved = test_session.query(Campaign).filter_by(campaign_name="Q1 Product Launch").first()
    assert retrieved is not None
    assert retrieved.campaign_id is not None
    assert retrieved.id == retrieved.campaign_id
    assert str(uuid.UUID(retrieved.campaign_id)) == retrieved.campaign_id
    assert retrieved.campaign_name == "Q1 Product Launch"
    assert retrieved.budget_amount == Decimal("50000.00")
    assert retrieved.spent_amount == Decimal("12500.50")
    assert retrieved.start_timestamp == start_time
    assert retrieved.end_timestamp == end_time
    assert isinstance(retrieved.created_timestamp, datetime)


def test_campaign_default_values(test_session: Session):
    """Verify default budget amounts and nullable timestamps for Campaign."""
    campaign = Campaign(
        campaign_name="Minimal Campaign",
    )
    test_session.add(campaign)
    test_session.commit()

    retrieved = test_session.query(Campaign).filter_by(campaign_name="Minimal Campaign").first()
    assert retrieved is not None
    assert retrieved.budget_amount == Decimal("0.00")
    assert retrieved.spent_amount == Decimal("0.00")
    assert retrieved.start_timestamp is None
    assert retrieved.end_timestamp is None
    assert isinstance(retrieved.created_timestamp, datetime)


def test_campaign_lead_relationship(test_session: Session):
    """Verify relationship between Campaign and Lead entities."""
    campaign = Campaign(
        campaign_name="Summer Promotion",
        budget_amount=Decimal("10000.00"),
    )
    source = LeadSource(
        source_name="Paid Social",
        cost_amount=Decimal("2000.00"),
    )
    test_session.add_all([campaign, source])
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=90,
        status_stage="NEW",
        deal_value=Decimal("5000.00"),
    )
    test_session.add(lead)
    test_session.commit()

    retrieved_campaign = test_session.query(Campaign).filter_by(campaign_id=campaign.campaign_id).first()
    retrieved_lead = test_session.query(Lead).filter_by(lead_id=lead.lead_id).first()

    assert retrieved_campaign is not None
    assert retrieved_lead is not None
    assert len(retrieved_campaign.leads) == 1
    assert retrieved_campaign.leads[0].lead_id == lead.lead_id
    assert retrieved_lead.campaign is not None
    assert retrieved_lead.campaign.campaign_name == "Summer Promotion"
