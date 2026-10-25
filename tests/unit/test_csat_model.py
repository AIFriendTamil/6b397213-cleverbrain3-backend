"""Unit tests for CSATResponse ORM entity model."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.csat import CSATResponse
from src.models.ticket import Ticket, TicketSeverity, TicketStatus


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_csat_response_creation_and_persistence(test_session: Session):
    """Verify CSATResponse creation, UUID primary key generation, and relationship to Ticket."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Account Access Restored",
        description="Password reset flow was blocked for user",
        severity=TicketSeverity.P3,
        status=TicketStatus.RESOLVED,
        organization_id="org_test",
        creator_user_id="usr_01",
        response_due_at=now + timedelta(hours=24),
        resolution_due_at=now + timedelta(hours=72),
        resolved_at=now,
    )
    test_session.add(ticket)
    test_session.commit()

    csat = CSATResponse(
        ticket_id=ticket.id,
        rating=5,
        feedback_text="Support agent resolved the issue within minutes. Great service!",
        user_id="usr_01",
    )
    test_session.add(csat)
    test_session.commit()

    retrieved = test_session.get(CSATResponse, csat.id)
    assert retrieved is not None
    assert str(uuid.UUID(retrieved.id)) == retrieved.id
    assert retrieved.ticket_id == ticket.id
    assert retrieved.rating == 5
    assert retrieved.feedback_text == "Support agent resolved the issue within minutes. Great service!"
    assert retrieved.user_id == "usr_01"
    assert isinstance(retrieved.submitted_at, datetime)
    assert repr(retrieved) == (
        f"<CSATResponse id={retrieved.id!r} ticket_id={ticket.id!r} "
        f"rating=5 user_id='usr_01'>"
    )

    # Test bidirectional relationship
    retrieved_ticket = test_session.get(Ticket, ticket.id)
    assert retrieved_ticket.csat_response is not None
    assert retrieved_ticket.csat_response.id == csat.id
    assert retrieved_ticket.csat_response.ticket is retrieved_ticket


def test_csat_response_unique_ticket_id_constraint(test_session: Session):
    """Verify that duplicate CSATResponse for the same ticket_id raises IntegrityError."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Email Notification Failure",
        description="SMTP credentials failed authentication",
        severity=TicketSeverity.P2,
        status=TicketStatus.CLOSED,
        organization_id="org_test",
        creator_user_id="usr_02",
        response_due_at=now + timedelta(hours=4),
        resolution_due_at=now + timedelta(hours=24),
    )
    test_session.add(ticket)
    test_session.commit()

    csat1 = CSATResponse(
        ticket_id=ticket.id,
        rating=4,
        feedback_text="Good resolution",
        user_id="usr_02",
    )
    test_session.add(csat1)
    test_session.commit()

    # Attempting to add a second CSAT response for the same ticket should fail
    csat2 = CSATResponse(
        ticket_id=ticket.id,
        rating=2,
        feedback_text="Submitting again",
        user_id="usr_02",
    )
    test_session.add(csat2)
    with pytest.raises(IntegrityError):
        test_session.commit()
    test_session.rollback()


def test_csat_response_optional_feedback(test_session: Session):
    """Verify creating CSATResponse without optional feedback_text."""
    now = datetime.now(timezone.utc)
    ticket = Ticket(
        title="Billing Error",
        description="Duplicate charge on monthly subscription",
        severity=TicketSeverity.P1,
        status=TicketStatus.RESOLVED,
        organization_id="org_test",
        creator_user_id="usr_03",
        response_due_at=now + timedelta(hours=1),
        resolution_due_at=now + timedelta(hours=4),
    )
    test_session.add(ticket)
    test_session.commit()

    csat = CSATResponse(
        ticket_id=ticket.id,
        rating=3,
        user_id="usr_03",
    )
    test_session.add(csat)
    test_session.commit()

    retrieved = test_session.get(CSATResponse, csat.id)
    assert retrieved is not None
    assert retrieved.rating == 3
    assert retrieved.feedback_text is None
