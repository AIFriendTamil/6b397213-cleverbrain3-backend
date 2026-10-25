"""Unit tests for FunnelStage ORM entity model."""

import uuid
from datetime import datetime
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.funnel import FunnelStage


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_funnel_stage_creation_and_persistence(test_session: Session):
    """Verify FunnelStage creation, attributes, target conversion rate, and persistence."""
    stage = FunnelStage(
        stage_name="Lead Captured",
        stage_order=1,
        target_conversion_rate=Decimal("25.50"),
    )
    test_session.add(stage)
    test_session.commit()

    retrieved = test_session.query(FunnelStage).filter_by(stage_name="Lead Captured").first()
    assert retrieved is not None
    assert retrieved.stage_id is not None
    assert retrieved.id == retrieved.stage_id
    assert str(uuid.UUID(retrieved.stage_id)) == retrieved.stage_id
    assert retrieved.stage_name == "Lead Captured"
    assert retrieved.stage_order == 1
    assert retrieved.target_conversion_rate == Decimal("25.50")
    assert isinstance(retrieved.created_timestamp, datetime)
    assert "Lead Captured" in repr(retrieved)


def test_funnel_stage_default_values(test_session: Session):
    """Verify default stage_order and target_conversion_rate values for FunnelStage."""
    stage = FunnelStage(
        stage_name="Initial Contact",
    )
    test_session.add(stage)
    test_session.commit()

    retrieved = test_session.query(FunnelStage).filter_by(stage_name="Initial Contact").first()
    assert retrieved is not None
    assert retrieved.stage_order == 0
    assert retrieved.target_conversion_rate == Decimal("0.00")
    assert isinstance(retrieved.created_timestamp, datetime)


def test_funnel_stage_sequential_ordering(test_session: Session):
    """Verify FunnelStage records can be retrieved in sequential order by stage_order."""
    stage3 = FunnelStage(stage_name="Proposal Sent", stage_order=3, target_conversion_rate=Decimal("60.00"))
    stage1 = FunnelStage(stage_name="Inquiry", stage_order=1, target_conversion_rate=Decimal("20.00"))
    stage2 = FunnelStage(stage_name="Qualification", stage_order=2, target_conversion_rate=Decimal("40.00"))

    test_session.add_all([stage3, stage1, stage2])
    test_session.commit()

    ordered_stages = test_session.query(FunnelStage).order_by(FunnelStage.stage_order.asc()).all()
    assert len(ordered_stages) == 3
    assert [s.stage_name for s in ordered_stages] == ["Inquiry", "Qualification", "Proposal Sent"]
    assert [s.stage_order for s in ordered_stages] == [1, 2, 3]
