"""Domain service for synthesizing meeting preparation briefs from lead interaction history and background metrics."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.models.lead import Lead
from src.models.meeting import MeetingBrief, ScheduledMeeting

logger = logging.getLogger(__name__)


@dataclass
class MeetingBriefDetails:
    """Dataclass encapsulating synthesized meeting brief data and metrics."""

    brief_id: str
    meeting_id: str
    lead_id: str
    quality_score: int
    interaction_history_summary: str
    key_discussion_notes: str
    brief_summary_text: str
    created_timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert brief details instance to a dictionary representation."""
        data = asdict(self)
        if isinstance(self.created_timestamp, datetime):
            data["created_timestamp"] = self.created_timestamp.isoformat()
        return data


def synthesize_interaction_history_summary(
    lead: Lead,
    current_meeting_id: str,
    prior_meetings: Optional[List[ScheduledMeeting]] = None,
) -> str:
    """Synthesize a structured summary of lead background and interaction history.

    Args:
        lead: Lead ORM entity instance.
        current_meeting_id: UUID string of the active meeting.
        prior_meetings: Optional list of previous ScheduledMeeting instances for this lead.

    Returns:
        Formatted multi-line text summary detailing acquisition context and interaction timeline.
    """
    source_name = lead.source.source_name if lead.source else "Direct Outreach / Unspecified"
    campaign_name = lead.campaign.campaign_name if lead.campaign else "None"
    region = lead.region_name if lead.region_name else "Unmapped Region"
    deal_val = float(lead.deal_value) if lead.deal_value is not None else 0.0

    created_str = (
        lead.created_timestamp.strftime("%Y-%m-%d %H:%M UTC")
        if lead.created_timestamp
        else "Unknown"
    )

    lines = [
        "Lead Profile & Context:",
        f"- Source: {source_name}",
        f"- Campaign: {campaign_name}",
        f"- Region: {region}",
        f"- Quality Score: {lead.quality_score}/100",
        f"- Funnel Stage: {lead.status_stage}",
        f"- Potential Deal Value: ${deal_val:,.2f}",
        f"- Lead Created: {created_str}",
    ]

    past_meetings = [
        m for m in (prior_meetings or [])
        if m.meeting_id != current_meeting_id
    ]

    if not past_meetings:
        lines.append("\nInteraction History:")
        lines.append("- No prior meetings recorded. This is the initial scheduled interaction with the lead.")
    else:
        lines.append(f"\nInteraction History ({len(past_meetings)} prior meeting(s)):")
        sorted_meetings = sorted(
            past_meetings,
            key=lambda m: m.start_timestamp if m.start_timestamp else datetime.min.replace(tzinfo=timezone.utc),
        )
        for idx, pm in enumerate(sorted_meetings, start=1):
            meeting_time = (
                pm.start_timestamp.strftime("%Y-%m-%d %H:%M UTC")
                if pm.start_timestamp
                else "Unscheduled"
            )
            brief_note = f" - {pm.brief_summary_text}" if pm.brief_summary_text else ""
            lines.append(f"{idx}. '{pm.title_text}' on {meeting_time}{brief_note}")

    return "\n".join(lines)


def synthesize_key_discussion_notes(
    lead: Lead,
    meeting: ScheduledMeeting,
) -> str:
    """Formulate key discussion points and preparation recommendations for the meeting.

    Args:
        lead: Lead ORM entity instance.
        meeting: ScheduledMeeting ORM entity instance.

    Returns:
        Structured text containing targeted discussion topics and recommended meeting focus.
    """
    meeting_title = meeting.title_text if meeting.title_text else "Sales Meeting"
    stage = (lead.status_stage or "NEW").upper()
    score = lead.quality_score or 0
    deal_val = float(lead.deal_value) if lead.deal_value is not None else 0.0

    notes = [f"Key Discussion Topics & Agenda for '{meeting_title}':"]

    if "NEW" in stage or "UNQUALIFIED" in stage:
        notes.append(
            "1. Discovery & Qualification: Identify core business pain points, confirm decision-making authority, and validate technical prerequisites."
        )
    elif "QUALIFIED" in stage:
        notes.append(
            "1. Requirements Mapping: Review solution architecture requirements, evaluate platform fit, and establish success criteria."
        )
    elif "DEMO" in stage:
        notes.append(
            "1. Product Demonstration: Highlight capability alignments, walk through key workflow scenarios, and address technical questions."
        )
    elif "PROPOSAL" in stage:
        notes.append(
            "1. Commercial Proposal: Present pricing tiers, ROI projections, SLA commitments, and implementation timeline."
        )
    elif "NEGOTIATION" in stage or "CLOSING" in stage:
        notes.append(
            "1. Contract Finalization: Address remaining commercial objections, confirm legal/compliance sign-offs, and finalize execution schedule."
        )
    else:
        notes.append(
            f"1. Stage Strategy ({lead.status_stage}): Review current progress, align on meeting goals, and establish mutually agreed next steps."
        )

    if score >= 70:
        notes.append(
            f"2. Priority Focus (High Quality Score: {score}/100): High conversion probability lead. Expedite executive involvement and proposal delivery."
        )
    elif score >= 40:
        notes.append(
            f"2. Nurturing Focus (Moderate Quality Score: {score}/100): Validate budget availability and clarify key evaluation timelines."
        )
    else:
        notes.append(
            f"2. Early Stage Focus (Lead Score: {score}/100): Focus on initial engagement and basic qualification before committing specialized technical resources."
        )

    if deal_val >= 10000.0:
        notes.append(
            f"3. High Value Opportunity (${deal_val:,.2f}): Ensure tailored value proposition presentation and note custom enterprise requirements."
        )
    else:
        notes.append(
            f"3. Standard Deal Context (${deal_val:,.2f}): Position standard solution packages and review timeline expectations."
        )

    return "\n".join(notes)


def synthesize_brief_summary_text(
    lead: Lead,
    meeting: ScheduledMeeting,
) -> str:
    """Generate a concise high-level brief summary text string.

    Args:
        lead: Lead ORM entity instance.
        meeting: ScheduledMeeting ORM entity instance.

    Returns:
        A concise summary text string.
    """
    title = meeting.title_text if meeting.title_text else "Meeting"
    region = lead.region_name if lead.region_name else "Unmapped Region"
    stage = lead.status_stage if lead.status_stage else "NEW"
    score = lead.quality_score or 0

    return (
        f"Preparation brief for '{title}' (Lead Quality Score: {score}, "
        f"Stage: {stage}, Region: {region})."
    )


def generate_meeting_brief(
    db: Session,
    meeting_id: str,
    force_refresh: bool = False,
) -> Optional[MeetingBriefDetails]:
    """Generate or retrieve synthesized preparation brief for a scheduled meeting.

    Args:
        db: Synchronous SQLAlchemy database session.
        meeting_id: UUID string of target scheduled meeting.
        force_refresh: If True, forces re-synthesis and update of existing brief.

    Returns:
        MeetingBriefDetails instance if meeting exists, or None if meeting not found.
    """
    stmt = (
        select(ScheduledMeeting)
        .options(
            joinedload(ScheduledMeeting.lead).joinedload(Lead.source),
            joinedload(ScheduledMeeting.lead).joinedload(Lead.campaign),
            joinedload(ScheduledMeeting.lead).joinedload(Lead.meetings),
            joinedload(ScheduledMeeting.brief),
        )
        .where(ScheduledMeeting.meeting_id == meeting_id)
    )
    meeting = db.scalar(stmt)

    if not meeting:
        logger.warning("Meeting brief generation requested for unknown meeting_id=%s", meeting_id)
        return None

    lead = meeting.lead
    if not lead:
        logger.warning("Meeting meeting_id=%s has no associated lead", meeting_id)
        return None

    existing_brief = meeting.brief
    if existing_brief and not force_refresh:
        return MeetingBriefDetails(
            brief_id=existing_brief.brief_id,
            meeting_id=existing_brief.meeting_id,
            lead_id=existing_brief.lead_id,
            quality_score=existing_brief.quality_score,
            interaction_history_summary=existing_brief.interaction_history_summary or "",
            key_discussion_notes=existing_brief.key_discussion_notes or "",
            brief_summary_text=existing_brief.brief_summary_text or "",
            created_timestamp=existing_brief.created_timestamp,
        )

    history_summary = synthesize_interaction_history_summary(
        lead=lead,
        current_meeting_id=meeting.meeting_id,
        prior_meetings=lead.meetings,
    )
    discussion_notes = synthesize_key_discussion_notes(
        lead=lead,
        meeting=meeting,
    )
    brief_summary = synthesize_brief_summary_text(
        lead=lead,
        meeting=meeting,
    )

    if existing_brief:
        existing_brief.quality_score = lead.quality_score
        existing_brief.interaction_history_summary = history_summary
        existing_brief.key_discussion_notes = discussion_notes
        existing_brief.brief_summary_text = brief_summary
        target_brief = existing_brief
    else:
        target_brief = MeetingBrief(
            meeting_id=meeting.meeting_id,
            lead_id=lead.lead_id,
            quality_score=lead.quality_score,
            interaction_history_summary=history_summary,
            key_discussion_notes=discussion_notes,
            brief_summary_text=brief_summary,
        )
        db.add(target_brief)

    meeting.brief_summary_text = brief_summary

    db.commit()
    db.refresh(target_brief)

    return MeetingBriefDetails(
        brief_id=target_brief.brief_id,
        meeting_id=target_brief.meeting_id,
        lead_id=target_brief.lead_id,
        quality_score=target_brief.quality_score,
        interaction_history_summary=target_brief.interaction_history_summary or "",
        key_discussion_notes=target_brief.key_discussion_notes or "",
        brief_summary_text=target_brief.brief_summary_text or "",
        created_timestamp=target_brief.created_timestamp,
    )


def get_meeting_brief(
    db: Session,
    meeting_id: str,
) -> Optional[MeetingBriefDetails]:
    """Retrieve synthesized meeting brief, generating one if not already created.

    Args:
        db: Synchronous SQLAlchemy database session.
        meeting_id: UUID string identifier of the meeting.

    Returns:
        MeetingBriefDetails instance if meeting exists, or None.
    """
    return generate_meeting_brief(db=db, meeting_id=meeting_id, force_refresh=False)
