"""Unit tests for Ticket ORM entity model and status/severity/escalation enums."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_ticket_enums():
    """Verify TicketStatus, TicketSeverity, and EscalationTier enum definitions."""
    assert TicketStatus.NEW.value == "NEW"
    assert TicketStatus.IN_PROGRESS.value == "IN_PROGRESS"
    assert TicketStatus.ESCALATED.value == "ESCALATED"
    assert TicketStatus.RESOLVED.value == "RESOLVED"
    assert TicketStatus.CLOSED.value == "CLOSED"

    assert TicketSeverity.P1.value == "P1"
    assert TicketSeverity.P2.value == "P2"
    assert TicketSeverity.P3.value == "P3"

    assert EscalationTier.TIER_1.value == 1
    assert EscalationTier.TIER_2.value == 2
    assert EscalationTier.TIER_3.value == 3


def test_ticket_creation_and_persistence(test_session: Session):
    """Verify Ticket creation, default values, UUID primary key generation, and retrieval."""
    now = datetime.now(timezone.utc)
    response_due = now + timedelta(hours=1)
    resolution_due = now + timedelta(hours=4)

    ticket = Ticket(
        title="Critical System Outage",
        description="Core service is unreachable for organization org_acme",
        severity=TicketSeverity.P1,
        organization_id="org_acme",
        creator_user_id="usr_creator_01",
        response_due_at=response_due,
        resolution_due_at=resolution_due,
    )

    test_session.add(ticket)
    test_session.commit()

    retrieved = test_session.query(Ticket).filter_by(title="Critical System Outage").first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.status == TicketStatus.NEW
    assert retrieved.severity == TicketSeverity.P1
    assert retrieved.escalation_tier == EscalationTier.TIER_1
    assert retrieved.organization_id == "org_acme"
    assert retrieved.creator_user_id == "usr_creator_01"
    assert retrieved.assigned_agent_id is None
    assert retrieved.is_sla_breached is False
    assert retrieved.resolved_at is None
    assert isinstance(retrieved.created_at, datetime)
    assert isinstance(retrieved.updated_at, datetime)
    assert repr(retrieved) == f"<Ticket id={retrieved.id!r} title='Critical System Outage' severity=TicketSeverity.P1 status=TicketStatus.NEW>"


def test_ticket_status_and_escalation_update(test_session: Session):
    """Verify updating ticket status, assigned agent, escalation tier, and resolved_at timestamp."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Payment Gateway Slowdown",
        description="Transactions are taking over 10 seconds",
        severity=TicketSeverity.P2,
        organization_id="org_beta",
        creator_user_id="usr_02",
        response_due_at=now + timedelta(hours=4),
        resolution_due_at=now + timedelta(hours=24),
    )
    test_session.add(ticket)
    test_session.commit()

    # Assign agent and transition to IN_PROGRESS
    ticket.assigned_agent_id = "agent_007"
    ticket.status = TicketStatus.IN_PROGRESS
    test_session.commit()

    retrieved = test_session.get(Ticket, ticket.id)
    assert retrieved.assigned_agent_id == "agent_007"
    assert retrieved.status == TicketStatus.IN_PROGRESS

    # Escalate ticket tier
    retrieved.status = TicketStatus.ESCALATED
    retrieved.escalation_tier = EscalationTier.TIER_2
    retrieved.is_sla_breached = True
    test_session.commit()

    retrieved = test_session.get(Ticket, ticket.id)
    assert retrieved.status == TicketStatus.ESCALATED
    assert retrieved.escalation_tier == 2
    assert retrieved.is_sla_breached is True

    # Resolve ticket
    resolved_time = datetime.now(timezone.utc)
    retrieved.status = TicketStatus.RESOLVED
    retrieved.resolved_at = resolved_time
    test_session.commit()

    retrieved = test_session.get(Ticket, ticket.id)
    assert retrieved.status == TicketStatus.RESOLVED
    assert retrieved.resolved_at is not None
