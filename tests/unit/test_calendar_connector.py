"""Unit tests for CalendarConnector in src/connectors/calendar_connector.py."""

from datetime import datetime, timezone
import pytest
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.connectors.calendar_connector import (
    CalendarConnector,
    ExternalCalendarConnector,
    parse_timestamp_iso,
)
from src.models.base import Base
from src.models.lead import Lead, LeadSource
from src.models.meeting import ScheduledMeeting
from src.services.meetings.calendar_port import (
    CalendarAdapterUnavailableError,
    CalendarEvent,
    CalendarPort,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for unit tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()

    # Create dummy lead source and lead for foreign keys
    source = LeadSource(source_name="Inbound Web")
    session.add(source)
    session.commit()
    session.refresh(source)

    lead = Lead(
        source_id=source.source_id,
        quality_score=85,
        status_stage="QUALIFIED",
        deal_value=25000.00,
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)

    yield session
    session.close()


def test_connector_inheritance():
    """Verify CalendarConnector inherits from CalendarPort and supports alias."""
    connector = CalendarConnector()
    assert isinstance(connector, CalendarPort)
    assert ExternalCalendarConnector is CalendarConnector


def test_calendar_event_to_dict():
    """Verify CalendarEvent.to_dict produces ISO format string for timestamps."""
    start_dt = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 11, 20, 11, 0, 0, tzinfo=timezone.utc)

    event = CalendarEvent(
        event_id="evt_123",
        title="Strategy Call",
        start_timestamp=start_dt,
        end_timestamp=end_dt,
        meeting_id="mtg_123",
        lead_id="lead_123",
    )

    data = event.to_dict()
    assert data["event_id"] == "evt_123"
    assert data["title"] == "Strategy Call"
    assert data["start_timestamp"] == "2026-11-20T10:00:00+00:00"
    assert data["end_timestamp"] == "2026-11-20T11:00:00+00:00"


def test_parse_timestamp_iso():
    """Verify parse_timestamp_iso handles datetime objects and ISO strings."""
    dt_now = datetime.now(timezone.utc)
    assert parse_timestamp_iso(dt_now) == dt_now

    parsed_z = parse_timestamp_iso("2026-11-20T15:30:00Z")
    assert parsed_z.year == 2026
    assert parsed_z.month == 11
    assert parsed_z.tzinfo == timezone.utc

    parsed_naive = parse_timestamp_iso(datetime(2026, 11, 20, 15, 30, 0))
    assert parsed_naive.tzinfo == timezone.utc


def test_fetch_events_mock_mode(db_session):
    """Verify fetch_events in mock mode retrieves local ScheduledMeeting records."""
    lead = db_session.query(Lead).first()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Mock Mode Meeting",
        start_timestamp=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),
        brief_summary_text="Initial alignment discussion",
    )
    db_session.add(meeting)
    db_session.commit()

    connector = CalendarConnector(provider="mock")
    events = connector.fetch_events(db_session=db_session)

    assert len(events) == 1
    assert events[0].title == "Mock Mode Meeting"
    assert events[0].lead_id == lead.lead_id
    assert events[0].is_external is False
    assert events[0].is_fallback is False


def test_fetch_events_external_success():
    """Verify fetch_events in external mode parses API HTTP response."""
    mock_payload = [
        {
            "event_id": "ext_evt_001",
            "meeting_id": "mtg_001",
            "lead_id": "lead_001",
            "title": "External Demo Call",
            "start_timestamp": "2026-11-20T16:00:00+00:00",
            "end_timestamp": "2026-11-20T17:00:00+00:00",
            "description": "Platform walkthrough",
            "location": "https://meet.example.com/demo",
            "status": "CONFIRMED",
        }
    ]

    def custom_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/calendar/events"
        return httpx.Response(200, json=mock_payload)

    transport = httpx.MockTransport(custom_handler)
    client = httpx.Client(transport=transport)

    connector = CalendarConnector(
        api_url="http://test-server/api/v1/calendar",
        provider="external",
        http_client=client,
    )

    events = connector.fetch_events()
    assert len(events) == 1
    assert events[0].event_id == "ext_evt_001"
    assert events[0].title == "External Demo Call"
    assert events[0].is_external is True
    assert events[0].is_fallback is False


def test_fetch_events_external_failure_with_fallback(db_session):
    """Verify external API timeout triggers graceful local database fallback."""
    lead = db_session.query(Lead).first()

    local_mtg = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Fallback Meeting",
        start_timestamp=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc),
    )
    db_session.add(local_mtg)
    db_session.commit()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out", request=request)

    transport = httpx.MockTransport(timeout_handler)
    client = httpx.Client(transport=transport)

    connector = CalendarConnector(
        api_url="http://test-server/api/v1/calendar",
        provider="external",
        fallback_to_local=True,
        http_client=client,
    )

    events = connector.fetch_events(db_session=db_session)
    assert len(events) == 1
    assert events[0].title == "Fallback Meeting"
    assert events[0].is_fallback is True


def test_fetch_events_external_failure_no_fallback():
    """Verify error raised when external API fails and fallback is disabled."""

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(error_handler)
    client = httpx.Client(transport=transport)

    connector = CalendarConnector(
        api_url="http://test-server/api/v1/calendar",
        provider="external",
        fallback_to_local=False,
        http_client=client,
    )

    with pytest.raises(CalendarAdapterUnavailableError, match="HTTP 503"):
        connector.fetch_events()


def test_create_event_mock_mode(db_session):
    """Verify create_event in mock mode creates ScheduledMeeting in database."""
    lead = db_session.query(Lead).first()

    connector = CalendarConnector(provider="mock")
    event = connector.create_event(
        title="New Lead Intro",
        start_timestamp=datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 11, 21, 11, 0, tzinfo=timezone.utc),
        lead_id=lead.lead_id,
        description="Introductory conversation",
        db_session=db_session,
    )

    assert event.title == "New Lead Intro"
    assert event.lead_id == lead.lead_id

    # Confirm record created in DB
    mtg_in_db = db_session.query(ScheduledMeeting).filter_by(lead_id=lead.lead_id).first()
    assert mtg_in_db is not None
    assert mtg_in_db.title_text == "New Lead Intro"


def test_sync_events_success(db_session):
    """Verify sync_events synchronizes external calendar events into local database."""
    lead = db_session.query(Lead).first()

    mock_payload = [
        {
            "event_id": "ext_sync_001",
            "lead_id": lead.lead_id,
            "title": "Synced External Meeting",
            "start_timestamp": "2026-11-22T14:00:00+00:00",
            "end_timestamp": "2026-11-22T15:00:00+00:00",
            "description": "Synced via Celery worker",
        }
    ]

    def sync_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_payload)

    transport = httpx.MockTransport(sync_handler)
    client = httpx.Client(transport=transport)

    connector = CalendarConnector(
        api_url="http://test-server/api/v1/calendar",
        provider="external",
        http_client=client,
    )

    synced_events = connector.sync_events(db_session=db_session)
    assert len(synced_events) == 1

    # Check local DB updated
    local_mtg = db_session.query(ScheduledMeeting).filter_by(calendar_event_id="ext_sync_001").first()
    assert local_mtg is not None
    assert local_mtg.title_text == "Synced External Meeting"
