"""Unit tests for support ticket management service."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.audit_log import TicketEvent, TicketEventType
from src.models.base import Base, create_db_engine
from src.models.ticket import EscalationTier, Ticket, TicketSeverity, TicketStatus
from src.services.escalation.storage_port import (
    AttachmentStoragePort,
    AttachmentValidationError,
)
from src.services.escalation.ticket_service import (
    TicketNotFoundError,
    TicketPermissionDeniedError,
    TicketValidationError,
    add_ticket_event,
    calculate_sla_due_dates,
    check_organization_access,
    create_ticket,
    delete_ticket,
    get_ticket_by_id,
    get_ticket_events,
    list_tickets,
    update_ticket_status,
)


class MockStorageAdapter(AttachmentStoragePort):
    """Mock storage adapter for unit testing attachment operations."""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.saved_files = []

    def validate_attachment(self, filename: str, content: bytes, max_size_bytes=None, allowed_extensions=None):
        if self.should_fail:
            raise AttachmentValidationError("File validation failed in mock.")
        if not filename or len(content) == 0:
            raise AttachmentValidationError("Empty file or filename.")

    def save_attachment(self, filename: str, content: bytes, ticket_id=None):
        self.validate_attachment(filename, content)
        meta = {
            "filename": filename,
            "stored_filename": f"uuid_{filename}",
            "file_path": f"/tmp/storage/{filename}",
            "file_size_bytes": len(content),
            "file_hash": "mock_hash_123",
            "attachment_url": f"/api/v1/attachments/uuid_{filename}",
        }
        self.saved_files.append(meta)
        return meta

    def get_attachment(self, file_path_or_id: str) -> bytes:
        return b"mock file content"

    def delete_attachment(self, file_path_or_id: str) -> bool:
        return True

    def exists(self, file_path_or_id: str) -> bool:
        return True

    def compute_hash(self, content: bytes) -> str:
        return "mock_hash_123"


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_calculate_sla_due_dates():
    """Verify SLA target due date calculation across P1, P2, and P3 severities."""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # P1: 1h response, 4h resolution
    resp_p1, res_p1 = calculate_sla_due_dates(TicketSeverity.P1, created_at=now)
    assert resp_p1 == now + timedelta(hours=1)
    assert res_p1 == now + timedelta(hours=4)

    # P2: 4h response, 24h resolution
    resp_p2, res_p2 = calculate_sla_due_dates(TicketSeverity.P2, created_at=now)
    assert resp_p2 == now + timedelta(hours=4)
    assert res_p2 == now + timedelta(hours=24)

    # P3: 24h response, 72h resolution
    resp_p3, res_p3 = calculate_sla_due_dates(TicketSeverity.P3, created_at=now)
    assert resp_p3 == now + timedelta(hours=24)
    assert res_p3 == now + timedelta(hours=72)


def test_check_organization_access():
    """Verify organization boundary permission checks."""
    ticket = Ticket(
        title="Test Ticket",
        description="Desc",
        severity=TicketSeverity.P2,
        organization_id="org_acme",
        creator_user_id="usr_01",
        response_due_at=datetime.now(timezone.utc),
        resolution_due_at=datetime.now(timezone.utc),
    )

    # Same org passes
    assert check_organization_access(ticket, "org_acme") is True

    # Wildcards pass
    assert check_organization_access(ticket, "admin") is True
    assert check_organization_access(ticket, "superadmin") is True
    assert check_organization_access(ticket, "*") is True
    assert check_organization_access(ticket, "all") is True

    # None or empty context passes (internal service)
    assert check_organization_access(ticket, None) is True

    # Mismatched org raises TicketPermissionDeniedError
    with pytest.raises(TicketPermissionDeniedError) as exc_info:
        check_organization_access(ticket, "org_other")
    assert "cannot access ticket in organization 'org_acme'" in str(exc_info.value)


def test_create_ticket_success(test_session: Session):
    """Verify successful ticket creation with default status, SLA timestamps, and TICKET_CREATED event."""
    ticket = create_ticket(
        db=test_session,
        title="System Login Failures",
        description="Users in org_acme cannot log into dashboard.",
        severity="P1",
        organization_id="org_acme",
        creator_user_id="usr_alice",
    )

    assert ticket.id is not None
    assert ticket.title == "System Login Failures"
    assert ticket.severity == TicketSeverity.P1
    assert ticket.status == TicketStatus.NEW
    assert ticket.escalation_tier == EscalationTier.TIER_1
    assert ticket.organization_id == "org_acme"
    assert ticket.creator_user_id == "usr_alice"
    assert ticket.assigned_agent_id is None
    assert ticket.is_sla_breached is False

    events = get_ticket_events(test_session, ticket.id)
    assert len(events) == 1
    assert events[0].event_type == TicketEventType.TICKET_CREATED
    assert events[0].author_id == "usr_alice"
    assert "created with severity P1" in events[0].content


def test_create_ticket_validation_failures(test_session: Session):
    """Verify validation checks on empty fields or invalid severity."""
    with pytest.raises(TicketValidationError) as exc:
        create_ticket(test_session, "", "desc", "P1", "org1", "usr1")
    assert "title cannot be empty" in str(exc.value)

    with pytest.raises(TicketValidationError) as exc:
        create_ticket(test_session, "title", "   ", "P1", "org1", "usr1")
    assert "description cannot be empty" in str(exc.value)

    with pytest.raises(TicketValidationError) as exc:
        create_ticket(test_session, "title", "desc", "P1", "  ", "usr1")
    assert "Organization ID cannot be empty" in str(exc.value)

    with pytest.raises(TicketValidationError) as exc:
        create_ticket(test_session, "title", "desc", "P1", "org1", "")
    assert "Creator user ID cannot be empty" in str(exc.value)

    with pytest.raises(TicketValidationError) as exc:
        create_ticket(test_session, "title", "desc", "INVALID_SEV", "org1", "usr1")
    assert "Invalid ticket severity" in str(exc.value)


def test_create_ticket_with_attachment(test_session: Session):
    """Verify ticket creation with initial file attachment."""
    mock_storage = MockStorageAdapter()
    ticket = create_ticket(
        db=test_session,
        title="Error Screenshot Included",
        description="Please check attached log screenshot.",
        severity=TicketSeverity.P2,
        organization_id="org_beta",
        creator_user_id="usr_bob",
        attachment_filename="error_log.png",
        attachment_content=b"PNG_HEADER_DATA_12345",
        storage_port=mock_storage,
    )

    assert len(mock_storage.saved_files) == 1
    assert mock_storage.saved_files[0]["filename"] == "error_log.png"

    events = get_ticket_events(test_session, ticket.id)
    assert len(events) == 1
    assert events[0].event_type == TicketEventType.TICKET_CREATED
    assert events[0].attachment_url == "/api/v1/attachments/uuid_error_log.png"
    assert events[0].attachment_filename == "uuid_error_log.png"


def test_get_ticket_by_id_and_permissions(test_session: Session):
    """Verify retrieving ticket by ID and permission enforcement."""
    ticket = create_ticket(
        db=test_session,
        title="Database Latency Spike",
        description="High query latencies",
        severity="P2",
        organization_id="org_gamma",
        creator_user_id="usr_charlie",
    )

    retrieved = get_ticket_by_id(test_session, ticket.id, user_organization_id="org_gamma")
    assert retrieved.id == ticket.id

    with pytest.raises(TicketNotFoundError):
        get_ticket_by_id(test_session, "non_existent_uuid")

    with pytest.raises(TicketPermissionDeniedError):
        get_ticket_by_id(test_session, ticket.id, user_organization_id="org_delta")


def test_list_tickets_filtering_and_pagination(test_session: Session):
    """Verify list_tickets filtering by organization, status, severity, and pagination."""
    t1 = create_ticket(test_session, "Bug A", "Desc A", "P1", "org_1", "usr_1")
    t2 = create_ticket(test_session, "Bug B", "Desc B", "P2", "org_1", "usr_2")
    t3 = create_ticket(test_session, "Bug C", "Desc C", "P3", "org_2", "usr_3")

    # Filter by org_1
    tickets, count = list_tickets(test_session, organization_id="org_1")
    assert count == 2
    assert {t.id for t in tickets} == {t1.id, t2.id}

    # Filter by severity P1
    tickets, count = list_tickets(test_session, severity="P1")
    assert count == 1
    assert tickets[0].id == t1.id

    # Test pagination
    tickets, count = list_tickets(test_session, limit=1, page=1)
    assert count == 3
    assert len(tickets) == 1


def test_update_ticket_status_progression(test_session: Session):
    """Verify ticket status lifecycle transitions and resolved_at timestamp setting."""
    ticket = create_ticket(
        test_session,
        title="API Gateway Timeout",
        description="Timeouts on POST /login",
        severity="P1",
        organization_id="org_1",
        creator_user_id="usr_1",
    )

    # Transition to IN_PROGRESS and assign agent
    updated = update_ticket_status(
        test_session,
        ticket.id,
        author_id="agent_007",
        new_status="IN_PROGRESS",
        assigned_agent_id="agent_007",
        comment="Taking ownership of this P1 incident.",
    )
    assert updated.status == TicketStatus.IN_PROGRESS
    assert updated.assigned_agent_id == "agent_007"
    assert updated.resolved_at is None

    # Transition to RESOLVED
    updated = update_ticket_status(
        test_session,
        ticket.id,
        author_id="agent_007",
        new_status="RESOLVED",
        comment="Applied rate limit fix to API gateway.",
    )
    assert updated.status == TicketStatus.RESOLVED
    assert isinstance(updated.resolved_at, datetime)

    # Reopen to IN_PROGRESS resets resolved_at
    updated = update_ticket_status(
        test_session,
        ticket.id,
        author_id="usr_1",
        new_status="IN_PROGRESS",
        comment="Issue reoccurred after deploy.",
    )
    assert updated.status == TicketStatus.IN_PROGRESS
    assert updated.resolved_at is None

    # Verify event audit trail
    events = get_ticket_events(test_session, ticket.id)
    assert len(events) == 5  # Created + Status(IN_PROGRESS) + Agent(agent_007) + Status(RESOLVED) + Status(IN_PROGRESS)


def test_update_ticket_severity_mid_lifecycle_recalibration(test_session: Session):
    """Verify EC-01: Mid-lifecycle severity upgrade (P3 -> P1) dynamically recalibrates SLA target timestamps."""
    created_time = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
    ticket = create_ticket(
        test_session,
        title="Minor Glitch Upgraded to Outage",
        description="Initially reported as low impact",
        severity="P3",
        organization_id="org_1",
        creator_user_id="usr_1",
    )
    # Manually override created_at to created_time for precise test math
    ticket.created_at = created_time
    ticket.response_due_at = created_time + timedelta(hours=24)
    ticket.resolution_due_at = created_time + timedelta(hours=72)
    test_session.commit()

    # Reclassify severity from P3 to P1
    updated = update_ticket_status(
        test_session,
        ticket.id,
        author_id="agent_lead",
        new_severity="P1",
        comment="Escalated to P1 outage based on customer impact.",
    )

    assert updated.severity == TicketSeverity.P1
    # Recalibrated from created_time: P1 response = 1h, resolution = 4h
    assert updated.response_due_at == created_time + timedelta(hours=1)
    assert updated.resolution_due_at == created_time + timedelta(hours=4)

    events = get_ticket_events(test_session, ticket.id)
    sev_event = [e for e in events if "Severity reclassified" in (e.content or "")][0]
    assert sev_event is not None
    assert sev_event.author_id == "agent_lead"


def test_add_ticket_event_comments_and_attachments(test_session: Session):
    """Verify FR-07 & EC-02: adding comment and attachment events to chronological audit trail."""
    ticket = create_ticket(
        test_session,
        title="SSL Certificate Expiry Warning",
        description="Cert expires in 7 days",
        severity="P2",
        organization_id="org_1",
        creator_user_id="usr_1",
    )

    mock_storage = MockStorageAdapter()

    # Add comment reply
    e1 = add_ticket_event(
        test_session,
        ticket_id=ticket.id,
        author_id="usr_1",
        content="Added renewed cert bundle to ticket.",
    )
    assert e1.event_type == TicketEventType.COMMENT
    assert e1.content == "Added renewed cert bundle to ticket."

    # Add attachment reply
    e2 = add_ticket_event(
        test_session,
        ticket_id=ticket.id,
        author_id="agent_1",
        content="Here is the verification cert log.",
        attachment_filename="cert_log.txt",
        attachment_content=b"CERT_VERIFIED_OK",
        storage_port=mock_storage,
    )
    assert e2.event_type == TicketEventType.ATTACHMENT_ADDED
    assert e2.attachment_filename == "uuid_cert_log.txt"

    # Verify chronological event ordering
    events = get_ticket_events(test_session, ticket.id)
    assert len(events) == 3  # Created, Comment, Attachment
    assert events[0].event_type == TicketEventType.TICKET_CREATED
    assert events[1].event_type == TicketEventType.COMMENT
    assert events[2].event_type == TicketEventType.ATTACHMENT_ADDED


def test_add_ticket_event_validation(test_session: Session):
    """Verify error raised when adding event without content or attachment."""
    ticket = create_ticket(
        test_session,
        title="Test Ticket",
        description="Desc",
        severity="P3",
        organization_id="org_1",
        creator_user_id="usr_1",
    )

    with pytest.raises(TicketValidationError) as exc:
        add_ticket_event(test_session, ticket.id, author_id="usr_1")
    assert "must contain text content or a file attachment" in str(exc.value)


def test_delete_ticket(test_session: Session):
    """Verify deleting a ticket and cascade removal."""
    ticket = create_ticket(
        test_session,
        title="Temporary Ticket",
        description="To be deleted",
        severity="P3",
        organization_id="org_1",
        creator_user_id="usr_1",
    )

    delete_ticket(test_session, ticket.id, user_organization_id="org_1")

    with pytest.raises(TicketNotFoundError):
        get_ticket_by_id(test_session, ticket.id)
