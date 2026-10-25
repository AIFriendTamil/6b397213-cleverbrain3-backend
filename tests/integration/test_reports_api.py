"""Integration tests for operational performance reporting API endpoints in backend/src/api/reports.py."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from src.api.main import create_app
from src.models.base import Base, create_db_engine, get_db
from src.models.csat import CSATResponse
from src.models.session import Session as SessionModel
from src.models.tenant import Tenant as TenantModel
from src.models.ticket import Ticket as TicketModel, TicketSeverity, TicketStatus
from src.models.user import User as UserModel


@pytest.fixture
def test_db():
    """Fixture providing an ephemeral in-memory SQLite database session."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with DBSession(engine) as session:
        yield session


@pytest.fixture
def primary_session(test_db):
    """Fixture creating primary tenant, user, and active session."""
    tenant = TenantModel(name="Primary Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="manager@primary.com",
        password_hash="hashed_pw",
        full_name="Primary Manager",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def other_session(test_db):
    """Fixture creating secondary tenant, user, and active session for isolation testing."""
    tenant = TenantModel(name="Other Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="manager@other.com",
        password_hash="hashed_pw",
        full_name="Other Manager",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id=tenant.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def admin_session(test_db):
    """Fixture creating admin session with wildcard tenant access."""
    tenant = TenantModel(name="Admin Tenant")
    test_db.add(tenant)
    test_db.flush()

    user = UserModel(
        tenant_id=tenant.id,
        email="admin@system.com",
        password_hash="hashed_pw",
        full_name="System Admin",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    session_record = SessionModel(
        user_id=user.id,
        tenant_id="admin",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    test_db.add(session_record)
    test_db.commit()
    test_db.refresh(session_record)
    return session_record


@pytest.fixture
def client(test_db):
    """Fixture overriding get_db dependency and returning a TestClient instance."""
    app_instance = create_app()

    def _get_db_override():
        yield test_db

    app_instance.dependency_overrides[get_db] = _get_db_override
    with TestClient(app_instance) as test_client:
        yield test_client
    app_instance.dependency_overrides.clear()


# --- Integration Tests ---


def test_unauthenticated_performance_report_returns_401(client):
    """Verify unauthenticated request to /api/v1/reports/performance returns 401 Unauthorized."""
    response = client.get("/api/v1/reports/performance")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_empty_performance_report(client, primary_session):
    """Verify performance report returns default metrics when no tickets exist for tenant."""
    headers = {"X-Session-ID": primary_session.id}
    response = client.get("/api/v1/reports/performance", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["organization_id"] == primary_session.tenant_id
    assert data["total_tickets"] == 0
    assert data["open_tickets"] == 0
    assert data["resolved_tickets"] == 0
    assert data["mttr"]["total_resolved_tickets"] == 0
    assert data["mttr"]["formatted_mttr"] == "0m"
    assert data["sla_compliance"]["total_tickets"] == 0
    assert data["sla_compliance"]["compliance_percentage"] == 100.0
    assert data["csat"]["total_responses"] == 0
    assert data["csat"]["average_rating"] == 0.0
    assert "P1" in data["severity_breakdown"]
    assert "P2" in data["severity_breakdown"]
    assert "P3" in data["severity_breakdown"]


def test_performance_report_with_tickets_and_csat(client, test_db, primary_session):
    """Verify MTTR, SLA compliance %, CSAT scores, and severity breakdown calculations."""
    now = datetime.now(timezone.utc)
    org_id = primary_session.tenant_id

    # Create resolved ticket 1 (P1, resolved in 2 hours, compliant)
    t1 = TicketModel(
        id="t-001",
        title="P1 Outage",
        description="Critical failure",
        severity=TicketSeverity.P1,
        status=TicketStatus.RESOLVED,
        organization_id=org_id,
        creator_user_id=primary_session.user_id,
        response_due_at=now + timedelta(hours=1),
        resolution_due_at=now + timedelta(hours=4),
        is_sla_breached=False,
        created_at=now - timedelta(hours=2),
        resolved_at=now,
    )
    test_db.add(t1)

    # CSAT for t1
    csat1 = CSATResponse(
        id="csat-001",
        ticket_id="t-001",
        rating=5,
        feedback_text="Great support!",
        user_id=primary_session.user_id,
        submitted_at=now,
    )
    test_db.add(csat1)

    # Create resolved ticket 2 (P2, resolved in 4 hours, breached)
    t2 = TicketModel(
        id="t-002",
        title="P2 Degradation",
        description="Performance issue",
        severity=TicketSeverity.P2,
        status=TicketStatus.CLOSED,
        organization_id=org_id,
        creator_user_id=primary_session.user_id,
        response_due_at=now - timedelta(hours=5),
        resolution_due_at=now - timedelta(hours=1),
        is_sla_breached=True,
        created_at=now - timedelta(hours=6),
        resolved_at=now - timedelta(hours=2),
    )
    test_db.add(t2)

    # CSAT for t2
    csat2 = CSATResponse(
        id="csat-002",
        ticket_id="t-002",
        rating=3,
        feedback_text="Ok support",
        user_id=primary_session.user_id,
        submitted_at=now,
    )
    test_db.add(csat2)

    # Create open ticket 3 (P3, unresolved)
    t3 = TicketModel(
        id="t-003",
        title="P3 Query",
        description="General question",
        severity=TicketSeverity.P3,
        status=TicketStatus.NEW,
        organization_id=org_id,
        creator_user_id=primary_session.user_id,
        response_due_at=now + timedelta(hours=24),
        resolution_due_at=now + timedelta(hours=72),
        is_sla_breached=False,
        created_at=now - timedelta(hours=1),
    )
    test_db.add(t3)
    test_db.commit()

    headers = {"X-Session-ID": primary_session.id}
    response = client.get("/api/v1/reports/performance", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["total_tickets"] == 3
    assert data["open_tickets"] == 1
    assert data["resolved_tickets"] == 2

    # MTTR: avg of 2h (7200s) and 4h (14400s) = 3h (10800s)
    assert data["mttr"]["total_resolved_tickets"] == 2
    assert data["mttr"]["average_seconds"] == 10800.0
    assert data["mttr"]["average_hours"] == 3.0

    # SLA Compliance: 3 total, 1 breached -> 2 compliant (66.67%)
    assert data["sla_compliance"]["total_tickets"] == 3
    assert data["sla_compliance"]["compliant_tickets"] == 2
    assert data["sla_compliance"]["breached_tickets"] == 1
    assert data["sla_compliance"]["compliance_percentage"] == 66.67

    # CSAT: 2 responses (ratings 5 and 3) -> avg 4.0, satisfied (rating >= 4) = 1 (50.0%)
    assert data["csat"]["total_responses"] == 2
    assert data["csat"]["average_rating"] == 4.0
    assert data["csat"]["satisfied_count"] == 1
    assert data["csat"]["satisfaction_percentage"] == 50.0

    # Severity Breakdown
    assert "P1" in data["severity_breakdown"]
    p1_data = data["severity_breakdown"]["P1"]
    assert p1_data["total_tickets"] == 1
    assert p1_data["mttr"]["total_resolved_tickets"] == 1
    assert p1_data["sla_compliance"]["compliance_percentage"] == 100.0
    assert p1_data["csat"]["average_rating"] == 5.0


def test_tenant_isolation_boundary_check(client, test_db, primary_session, other_session):
    """Verify user from primary tenant cannot query secondary tenant performance metrics."""
    headers = {"X-Session-ID": primary_session.id}
    params = {"organization_id": other_session.tenant_id}

    response = client.get("/api/v1/reports/performance", headers=headers, params=params)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    err_data = response.json()
    assert err_data["detail"]["code"] == "TENANT_MISMATCH"


def test_admin_tenant_access_allowed(client, admin_session, other_session):
    """Verify admin session can query performance metrics for any specific tenant."""
    headers = {"X-Session-ID": admin_session.id}
    params = {"organization_id": other_session.tenant_id}

    response = client.get("/api/v1/reports/performance", headers=headers, params=params)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["organization_id"] == other_session.tenant_id


def test_invalid_date_range_returns_400(client, primary_session):
    """Verify start_time > end_time returns 400 Bad Request with INVALID_DATE_RANGE code."""
    headers = {"X-Session-ID": primary_session.id}
    params = {
        "start_time": "2026-10-25T12:00:00Z",
        "end_time": "2026-10-20T12:00:00Z",
    }

    response = client.get("/api/v1/reports/performance", headers=headers, params=params)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    err_data = response.json()
    assert err_data["detail"]["code"] == "INVALID_DATE_RANGE"


def test_filter_by_severity(client, test_db, primary_session):
    """Verify performance report filtering by specific severity (P1)."""
    now = datetime.now(timezone.utc)
    org_id = primary_session.tenant_id

    # Add P1 ticket
    t1 = TicketModel(
        id="t-p1",
        title="P1 Ticket",
        description="Desc",
        severity=TicketSeverity.P1,
        status=TicketStatus.RESOLVED,
        organization_id=org_id,
        creator_user_id=primary_session.user_id,
        response_due_at=now,
        resolution_due_at=now,
        created_at=now - timedelta(hours=1),
        resolved_at=now,
    )
    # Add P3 ticket
    t2 = TicketModel(
        id="t-p3",
        title="P3 Ticket",
        description="Desc",
        severity=TicketSeverity.P3,
        status=TicketStatus.RESOLVED,
        organization_id=org_id,
        creator_user_id=primary_session.user_id,
        response_due_at=now,
        resolution_due_at=now,
        created_at=now - timedelta(hours=5),
        resolved_at=now,
    )
    test_db.add_all([t1, t2])
    test_db.commit()

    headers = {"X-Session-ID": primary_session.id}
    params = {"severity": "P1"}

    response = client.get("/api/v1/reports/performance", headers=headers, params=params)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["total_tickets"] == 1
    assert data["mttr"]["total_resolved_tickets"] == 1
    assert data["mttr"]["average_hours"] == 1.0
