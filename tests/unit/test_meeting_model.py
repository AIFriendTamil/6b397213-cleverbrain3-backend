"""Unit tests for ScheduledMeeting and MeetingBrief ORM entity models."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.lead import Lead, LeadSource
from src.models.meeting import MeetingBrief, ScheduledMeeting


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_scheduled_meeting_creation_and_persistence(test_session: Session):
    """Verify ScheduledMeeting creation, attributes, timestamps, and persistence."""
    source = LeadSource(source_name="Direct Outreach")
    test_session.add(source)
    test_session.commit()

    lead = Lead(source_id=source.source_id, quality_score=85)
    test_session.add(lead)
    test_session.commit()

    start_time = datetime(2026, 4, 15, 10, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 4, 15, 11, 0, 0, tzinfo=timezone.utc)

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Q2 Strategy Sync with Lead",
        start_timestamp=start_time,
        end_timestamp=end_time,
        calendar_event_id="cal_evt_998877",
        brief_summary_text="Initial alignment on enterprise license proposal.",
    )
    test_session.add(meeting)
    test_session.commit()

    retrieved = test_session.query(ScheduledMeeting).filter_by(title_text="Q2 Strategy Sync with Lead").first()
    assert retrieved is not None
    assert retrieved.meeting_id is not None
    assert retrieved.id == retrieved.meeting_id
    assert str(uuid.UUID(retrieved.meeting_id)) == retrieved.meeting_id
    assert retrieved.lead_id == lead.lead_id
    assert retrieved.start_timestamp == start_time
    assert retrieved.end_timestamp == end_time
    assert retrieved.calendar_event_id == "cal_evt_998877"
    assert retrieved.brief_summary_text == "Initial alignment on enterprise license proposal."
    assert isinstance(retrieved.created_timestamp, datetime)
    assert "Q2 Strategy Sync with Lead" in repr(retrieved)


def test_meeting_brief_creation_and_persistence(test_session: Session):
    """Verify MeetingBrief creation, attributes, quality_score, summaries, and persistence."""
    source = LeadSource(source_name="Inbound Web")
    test_session.add(source)
    test_session.commit()

    lead = Lead(source_id=source.source_id, quality_score=92)
    test_session.add(lead)
    test_session.commit()

    start_time = datetime(2026, 5, 1, 14, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 5, 1, 15, 0, 0, tzinfo=timezone.utc)

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Technical Demo",
        start_timestamp=start_time,
        end_timestamp=end_time,
    )
    test_session.add(meeting)
    test_session.commit()

    brief = MeetingBrief(
        meeting_id=meeting.meeting_id,
        lead_id=lead.lead_id,
        quality_score=92,
        interaction_history_summary="Exchanged 3 emails regarding cloud deployment specs.",
        key_discussion_notes="Focus on SSO integration and multi-tenant security.",
        brief_summary_text="High priority lead evaluating platform migration.",
    )
    test_session.add(brief)
    test_session.commit()

    retrieved_brief = test_session.query(MeetingBrief).filter_by(meeting_id=meeting.meeting_id).first()
    assert retrieved_brief is not None
    assert retrieved_brief.brief_id is not None
    assert retrieved_brief.id == retrieved_brief.brief_id
    assert str(uuid.UUID(retrieved_brief.brief_id)) == retrieved_brief.brief_id
    assert retrieved_brief.lead_id == lead.lead_id
    assert retrieved_brief.quality_score == 92
    assert "3 emails" in retrieved_brief.interaction_history_summary
    assert "SSO integration" in retrieved_brief.key_discussion_notes
    assert "High priority" in retrieved_brief.brief_summary_text
    assert isinstance(retrieved_brief.created_timestamp, datetime)
    assert retrieved_brief.brief_id in repr(retrieved_brief)


def test_meeting_and_brief_relationships(test_session: Session):
    """Verify ORM relationships between Lead, ScheduledMeeting, and MeetingBrief."""
    source = LeadSource(source_name="Partner Referral")
    test_session.add(source)
    test_session.commit()

    lead = Lead(source_id=source.source_id, quality_score=78)
    test_session.add(lead)
    test_session.commit()

    start_time = datetime(2026, 6, 10, 9, 30, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 6, 10, 10, 0, 0, tzinfo=timezone.utc)

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Introductory Call",
        start_timestamp=start_time,
        end_timestamp=end_time,
    )
    test_session.add(meeting)
    test_session.commit()

    brief = MeetingBrief(
        meeting_id=meeting.meeting_id,
        lead_id=lead.lead_id,
        quality_score=78,
        interaction_history_summary="Initial inquiry from website partner form.",
        key_discussion_notes="Evaluate fit for enterprise plan.",
    )
    test_session.add(brief)
    test_session.commit()

    # Query lead and verify relationship navigation
    retrieved_lead = test_session.query(Lead).filter_by(lead_id=lead.lead_id).first()
    assert retrieved_lead is not None
    assert len(retrieved_lead.meetings) == 1
    assert retrieved_lead.meetings[0].title_text == "Introductory Call"

    retrieved_meeting = retrieved_lead.meetings[0]
    assert retrieved_meeting.lead.lead_id == lead.lead_id
    assert retrieved_meeting.brief is not None
    assert retrieved_meeting.brief.brief_id == brief.brief_id
    assert retrieved_meeting.brief.meeting.meeting_id == meeting.meeting_id
    assert retrieved_meeting.brief.lead.lead_id == lead.lead_id


def test_scheduled_meeting_default_values(test_session: Session):
    """Verify default values for optional fields in ScheduledMeeting."""
    source = LeadSource(source_name="Organic Search")
    test_session.add(source)
    test_session.commit()

    lead = Lead(source_id=source.source_id)
    test_session.add(lead)
    test_session.commit()

    start_time = datetime(2026, 7, 1, 11, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 7, 1, 11, 30, 0, tzinfo=timezone.utc)

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Brief Standup",
        start_timestamp=start_time,
        end_timestamp=end_time,
    )
    test_session.add(meeting)
    test_session.commit()

    retrieved = test_session.query(ScheduledMeeting).filter_by(meeting_id=meeting.meeting_id).first()
    assert retrieved is not None
    assert retrieved.calendar_event_id is None
    assert retrieved.brief_summary_text is None
    assert retrieved.brief is None
