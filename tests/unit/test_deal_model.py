"""Unit tests for Deal, DealStage, Milestone, CriticalTask, and Stakeholder ORM entity models."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.deal import (
    CriticalTask,
    Deal,
    DealStage,
    Milestone,
    MilestoneStatus,
    Stakeholder,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_deal_stage_creation_and_persistence(test_session: Session):
    """Verify DealStage creation, default values, UUID generation, and retrieval."""
    stage = DealStage(
        name="Qualification",
        display_order=1,
    )
    test_session.add(stage)
    test_session.commit()

    retrieved = test_session.query(DealStage).filter_by(name="Qualification").first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.name == "Qualification"
    assert retrieved.display_order == 1
    assert isinstance(retrieved.created_at, datetime)
    assert isinstance(retrieved.updated_at, datetime)


def test_deal_stage_unique_name_constraint(test_session: Session):
    """Verify duplicate stage names trigger an IntegrityError."""
    stage1 = DealStage(name="Proposal", display_order=1)
    stage2 = DealStage(name="Proposal", display_order=2)
    test_session.add(stage1)
    test_session.commit()

    test_session.add(stage2)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()


def test_deal_creation_and_persistence(test_session: Session):
    """Verify Deal creation, relationship to DealStage, and attribute types."""
    stage = DealStage(name="Negotiation", display_order=2)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Enterprise License Agreement",
        monetary_value=Decimal("150000.50"),
        stage_id=stage.id,
        assigned_team_member_id="usr_12345",
        win_probability=0.75,
    )
    test_session.add(deal)
    test_session.commit()

    retrieved = test_session.query(Deal).filter_by(title="Enterprise License Agreement").first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.title == "Enterprise License Agreement"
    assert retrieved.monetary_value == Decimal("150000.50")
    assert retrieved.stage_id == stage.id
    assert retrieved.assigned_team_member_id == "usr_12345"
    assert retrieved.win_probability == 0.75
    assert retrieved.stage.id == stage.id
    assert retrieved.stage.name == "Negotiation"
    assert len(stage.deals) == 1
    assert stage.deals[0].id == deal.id


def test_deal_stage_cascade_delete(test_session: Session):
    """Verify deleting a DealStage deletes its associated Deals."""
    stage = DealStage(name="Closed Won", display_order=5)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Acme Expansion",
        monetary_value=Decimal("50000.00"),
        stage_id=stage.id,
        win_probability=1.0,
    )
    test_session.add(deal)
    test_session.commit()

    test_session.delete(stage)
    test_session.commit()

    retrieved_deal = test_session.query(Deal).filter_by(title="Acme Expansion").first()
    assert retrieved_deal is None


def test_milestone_and_critical_task_persistence(test_session: Session):
    """Verify Milestone and CriticalTask creation, relationships, and cascade deletion."""
    stage = DealStage(name="Discovery", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Global Security Audit",
        monetary_value=Decimal("80000.00"),
        stage_id=stage.id,
    )
    test_session.add(deal)
    test_session.commit()

    now_dt = datetime.now(timezone.utc)
    milestone = Milestone(
        deal_id=deal.id,
        title="Technical Validation",
        target_date=now_dt,
        status=MilestoneStatus.IN_PROGRESS,
    )
    test_session.add(milestone)
    test_session.commit()

    task = CriticalTask(
        milestone_id=milestone.id,
        title="SOC2 Compliance Review",
        due_date=now_dt,
        is_critical=True,
        is_overdue=False,
        escalated_to_supervisor=False,
    )
    test_session.add(task)
    test_session.commit()

    retrieved_m = test_session.query(Milestone).filter_by(id=milestone.id).first()
    assert retrieved_m is not None
    assert retrieved_m.title == "Technical Validation"
    assert retrieved_m.status == MilestoneStatus.IN_PROGRESS
    assert len(retrieved_m.critical_tasks) == 1
    assert retrieved_m.critical_tasks[0].title == "SOC2 Compliance Review"
    assert retrieved_m.critical_tasks[0].is_critical is True

    # Test deal back_populates
    assert len(deal.milestones) == 1
    assert deal.milestones[0].id == milestone.id

    # Test cascade delete deal -> milestone -> critical_task
    test_session.delete(deal)
    test_session.commit()

    assert test_session.query(Milestone).filter_by(id=milestone.id).first() is None
    assert test_session.query(CriticalTask).filter_by(id=task.id).first() is None


def test_stakeholder_persistence_and_hierarchy(test_session: Session):
    """Verify Stakeholder creation, parent-child relationships, and deal relationship."""
    stage = DealStage(name="Contract", display_order=3)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Cloud Migration Deal",
        monetary_value=Decimal("200000.00"),
        stage_id=stage.id,
    )
    test_session.add(deal)
    test_session.commit()

    exec_sponsor = Stakeholder(
        deal_id=deal.id,
        name="Jane Doe",
        org_role="Chief Information Officer",
        contact_email="jane.doe@example.com",
    )
    test_session.add(exec_sponsor)
    test_session.commit()

    tech_lead = Stakeholder(
        deal_id=deal.id,
        name="John Smith",
        org_role="Lead Systems Architect",
        contact_email="john.smith@example.com",
        parent_stakeholder_id=exec_sponsor.id,
    )
    test_session.add(tech_lead)
    test_session.commit()

    retrieved_exec = test_session.query(Stakeholder).filter_by(id=exec_sponsor.id).first()
    assert retrieved_exec is not None
    assert len(retrieved_exec.subordinates) == 1
    assert retrieved_exec.subordinates[0].name == "John Smith"

    retrieved_lead = test_session.query(Stakeholder).filter_by(id=tech_lead.id).first()
    assert retrieved_lead is not None
    assert retrieved_lead.parent_stakeholder is not None
    assert retrieved_lead.parent_stakeholder.id == exec_sponsor.id

    assert len(deal.stakeholders) == 2

