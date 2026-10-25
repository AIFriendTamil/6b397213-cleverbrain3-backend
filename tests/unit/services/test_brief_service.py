"""Unit tests for meeting brief synthesis service."""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.campaign import Campaign
from src.models.lead import Lead, LeadSource
from src.models.meeting import MeetingBrief, ScheduledMeeting
from src.services.meetings.brief_service import (
    MeetingBriefDetails,
    generate_meeting_brief,
    get_meeting_brief,
    synthesize_brief_summary_text,
    synthesize_interaction_history_summary,
    synthesize_key_discussion_notes,
)


@pytest.fixture
def test_session():
    """In-memory SQLite session fixture for brief service unit testing."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_synthesize_interaction_history_summary_no_prior(test_session: Session):
    """Verify history synthesis for a lead without prior meetings."""
    source = LeadSource(source_name="Organic Search")
    test_session.add(source)
    test_session.commit()

    campaign = Campaign(
        campaign_name="Q1 Product Launch",
        budget_amount=Decimal("5000.00"),
        spent_amount=Decimal("1200.00"),
        start_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )
    test_session.add(campaign)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        campaign_id=campaign.campaign_id,
        quality_score=88,
        status_stage="QUALIFIED",
        region_name="North America",
        deal_value=Decimal("15000.00"),
    )
    test_session.add(lead)
    test_session.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Discovery Call",
        start_timestamp=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 4, 10, 10, 30, tzinfo=timezone.utc),
    )
    test_session.add(meeting)
    test_session.commit()

    summary = synthesize_interaction_history_summary(
        lead=lead,
        current_meeting_id=meeting.meeting_id,
        prior_meetings=[],
    )

    assert "Lead Profile & Context:" in summary
    assert "Organic Search" in summary
    assert "Q1 Product Launch" in summary
    assert "North America" in summary
    assert "88/100" in summary
    assert "QUALIFIED" in summary
    assert "$15,000.00" in summary
    assert "No prior meetings recorded" in summary


def test_synthesize_interaction_history_summary_with_priors(test_session: Session):
    """Verify history synthesis for a lead with prior scheduled meetings."""
    source = LeadSource(source_name="Paid Ads")
    test_session.add(source)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=75,
        status_stage="DEMO",
        deal_value=Decimal("8000.00"),
    )
    test_session.add(lead)
    test_session.commit()

    meeting_1 = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Initial Introduction",
        start_timestamp=datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 3, 1, 14, 30, tzinfo=timezone.utc),
        brief_summary_text="Introductory alignment completed.",
    )
    meeting_2 = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Technical Review",
        start_timestamp=datetime(2026, 3, 15, 11, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        brief_summary_text="Technical architecture validated.",
    )
    meeting_active = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Executive Proposal Demo",
        start_timestamp=datetime(2026, 4, 1, 15, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 4, 1, 16, 0, tzinfo=timezone.utc),
    )
    test_session.add_all([meeting_1, meeting_2, meeting_active])
    test_session.commit()

    summary = synthesize_interaction_history_summary(
        lead=lead,
        current_meeting_id=meeting_active.meeting_id,
        prior_meetings=[meeting_1, meeting_2, meeting_active],
    )

    assert "2 prior meeting(s)" in summary
    assert "Initial Introduction" in summary
    assert "Technical Review" in summary
    assert "Introductory alignment completed" in summary
    assert "Technical architecture validated" in summary


def test_synthesize_key_discussion_notes(test_session: Session):
    """Verify key discussion notes logic across stages and score/deal thresholds."""
    source = LeadSource(source_name="Webinar")
    test_session.add(source)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=95,
        status_stage="PROPOSAL",
        deal_value=Decimal("25000.00"),
    )
    test_session.add(lead)
    test_session.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Commercial Terms Review",
        start_timestamp=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc),
    )

    notes = synthesize_key_discussion_notes(lead=lead, meeting=meeting)

    assert "Commercial Terms Review" in notes
    assert "Commercial Proposal" in notes
    assert "High Quality Score: 95/100" in notes
    assert "High Value Opportunity ($25,000.00)" in notes


def test_generate_meeting_brief_end_to_end(test_session: Session):
    """Verify end-to-end creation, synthesis, and persistence of MeetingBrief."""
    source = LeadSource(source_name="Partner Referral")
    test_session.add(source)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=82,
        status_stage="NEGOTIATION",
        region_name="Europe",
        deal_value=Decimal("12500.00"),
    )
    test_session.add(lead)
    test_session.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Final Contract Review",
        start_timestamp=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc),
    )
    test_session.add(meeting)
    test_session.commit()

    brief_details = generate_meeting_brief(test_session, meeting.meeting_id)

    assert brief_details is not None
    assert isinstance(brief_details, MeetingBriefDetails)
    assert brief_details.meeting_id == meeting.meeting_id
    assert brief_details.lead_id == lead.lead_id
    assert brief_details.quality_score == 82
    assert "Partner Referral" in brief_details.interaction_history_summary
    assert "Contract Finalization" in brief_details.key_discussion_notes
    assert "Preparation brief for 'Final Contract Review'" in brief_details.brief_summary_text

    # Verify persistence in database
    db_brief = test_session.query(MeetingBrief).filter_by(meeting_id=meeting.meeting_id).first()
    assert db_brief is not None
    assert db_brief.brief_id == brief_details.brief_id
    assert db_brief.brief_summary_text == brief_details.brief_summary_text

    # Verify ScheduledMeeting brief_summary_text is updated
    test_session.refresh(meeting)
    assert meeting.brief_summary_text == brief_details.brief_summary_text

    # Test get_meeting_brief returns existing brief
    fetched = get_meeting_brief(test_session, meeting.meeting_id)
    assert fetched is not None
    assert fetched.brief_id == brief_details.brief_id

    # Test to_dict conversion
    brief_dict = brief_details.to_dict()
    assert brief_dict["meeting_id"] == meeting.meeting_id
    assert isinstance(brief_dict["created_timestamp"], str)


def test_generate_meeting_brief_force_refresh(test_session: Session):
    """Verify force_refresh updates existing brief when lead properties change."""
    source = LeadSource(source_name="Inbound")
    test_session.add(source)
    test_session.commit()

    lead = Lead(
        source_id=source.source_id,
        quality_score=40,
        status_stage="NEW",
    )
    test_session.add(lead)
    test_session.commit()

    meeting = ScheduledMeeting(
        lead_id=lead.lead_id,
        title_text="Initial Lead Sync",
        start_timestamp=datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 4, 5, 10, 30, tzinfo=timezone.utc),
    )
    test_session.add(meeting)
    test_session.commit()

    initial_brief = generate_meeting_brief(test_session, meeting.meeting_id)
    assert initial_brief.quality_score == 40

    # Update lead status and score
    lead.quality_score = 90
    lead.status_stage = "DEMO"
    test_session.commit()

    # Without force_refresh, existing brief details returned
    cached_brief = generate_meeting_brief(test_session, meeting.meeting_id, force_refresh=False)
    assert cached_brief.quality_score == 40

    # With force_refresh, brief is re-synthesized
    refreshed_brief = generate_meeting_brief(test_session, meeting.meeting_id, force_refresh=True)
    assert refreshed_brief.quality_score == 90
    assert "DEMO" in refreshed_brief.interaction_history_summary


def test_generate_meeting_brief_nonexistent():
    """Verify handling when meeting_id is unknown."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        result = generate_meeting_brief(session, "nonexistent-meeting-id")
        assert result is None
