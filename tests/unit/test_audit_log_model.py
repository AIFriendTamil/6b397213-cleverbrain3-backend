"""Unit tests for TicketEvent ORM model and TicketEventType enum."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.audit_log import TicketEvent, TicketEventType
from src.models.base import Base, create_db_engine
from src.models.ticket import Ticket, TicketSeverity, TicketStatus


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_ticket_event_type_enum():
    """Verify TicketEventType enum member values."""
    assert TicketEventType.TICKET_CREATED.value == "TICKET_CREATED"
    assert TicketEventType.STATUS_CHANGE.value == "STATUS_CHANGE"
    assert TicketEventType.COMMENT.value == "COMMENT"
    assert TicketEventType.ESCALATION_TRIGGERED.value == "ESCALATION_TRIGGERED"
    assert TicketEventType.ATTACHMENT_ADDED.value == "ATTACHMENT_ADDED"
    assert TicketEventType.CSAT_SUBMITTED.value == "CSAT_SUBMITTED"


def test_ticket_event_creation_and_persistence(test_session: Session):
    """Verify TicketEvent creation, default UUID generation, and relationship to Ticket."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Login Service Degradation",
        description="Users experiencing timeout during authentication",
        severity=TicketSeverity.P2,
        organization_id="org_alpha",
        creator_user_id="usr_01",
        response_due_at=now + timedelta(hours=4),
        resolution_due_at=now + timedelta(hours=24),
    )
    test_session.add(ticket)
    test_session.commit()

    event = TicketEvent(
        ticket_id=ticket.id,
        author_id="usr_01",
        event_type=TicketEventType.TICKET_CREATED,
        content="Ticket created by user usr_01",
    )
    test_session.add(event)
    test_session.commit()

    retrieved_event = test_session.get(TicketEvent, event.id)
    assert retrieved_event is not None
    assert str(uuid.UUID(retrieved_event.id)) == retrieved_event.id
    assert retrieved_event.ticket_id == ticket.id
    assert retrieved_event.author_id == "usr_01"
    assert retrieved_event.event_type == TicketEventType.TICKET_CREATED
    assert retrieved_event.content == "Ticket created by user usr_01"
    assert retrieved_event.attachment_url is None
    assert retrieved_event.attachment_filename is None
    assert isinstance(retrieved_event.created_at, datetime)
    assert repr(retrieved_event) == (
        f"<TicketEvent id={retrieved_event.id!r} ticket_id={ticket.id!r} "
        f"event_type={TicketEventType.TICKET_CREATED}>"
    )

    # Test relationship from ticket to events
    retrieved_ticket = test_session.get(Ticket, ticket.id)
    assert len(retrieved_ticket.events) == 1
    assert retrieved_ticket.events[0].id == event.id
    assert retrieved_ticket.events[0].ticket is retrieved_ticket


def test_ticket_event_attachment_and_comment(test_session: Session):
    """Verify creating TicketEvent with comment and attachment metadata."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Database Connection Pool Exhaustion",
        description="High latency observed on SQL queries",
        severity=TicketSeverity.P1,
        organization_id="org_beta",
        creator_user_id="usr_admin",
        response_due_at=now + timedelta(hours=1),
        resolution_due_at=now + timedelta(hours=4),
    )
    test_session.add(ticket)
    test_session.commit()

    comment_event = TicketEvent(
        ticket_id=ticket.id,
        author_id="agent_42",
        event_type=TicketEventType.COMMENT,
        content="Investigating DB metrics in Grafana.",
    )
    attachment_event = TicketEvent(
        ticket_id=ticket.id,
        author_id="agent_42",
        event_type=TicketEventType.ATTACHMENT_ADDED,
        content="Attached query logs",
        attachment_url="https://storage.local/logs/db_dump.txt",
        attachment_filename="db_dump.txt",
    )
    test_session.add_all([comment_event, attachment_event])
    test_session.commit()

    retrieved_ticket = test_session.get(Ticket, ticket.id)
    assert len(retrieved_ticket.events) == 2
    event_types = [e.event_type for e in retrieved_ticket.events]
    assert TicketEventType.COMMENT in event_types
    assert TicketEventType.ATTACHMENT_ADDED in event_types

    att_event = [e for e in retrieved_ticket.events if e.event_type == TicketEventType.ATTACHMENT_ADDED][0]
    assert att_event.attachment_url == "https://storage.local/logs/db_dump.txt"
    assert att_event.attachment_filename == "db_dump.txt"


def test_ticket_event_cascade_delete(test_session: Session):
    """Verify that deleting a Ticket cascades and deletes associated TicketEvent records."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Temporary Ticket",
        description="To be deleted",
        severity=TicketSeverity.P3,
        organization_id="org_test",
        creator_user_id="usr_test",
        response_due_at=now + timedelta(hours=24),
        resolution_due_at=now + timedelta(hours=72),
    )
    test_session.add(ticket)
    test_session.commit()

    event = TicketEvent(
        ticket_id=ticket.id,
        author_id="usr_test",
        event_type=TicketEventType.TICKET_CREATED,
        content="Created temporary ticket",
    )
    test_session.add(event)
    test_session.commit()

    event_id = event.id
    test_session.delete(ticket)
    test_session.commit()

    assert test_session.get(TicketEvent, event_id) is None
