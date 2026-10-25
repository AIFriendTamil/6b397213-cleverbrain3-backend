"""FastAPI endpoint router and Pydantic validation schemas for scheduled meeting calendar and preparation briefs."""

import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from src.api.deps import get_current_session
from src.connectors.calendar_connector import CalendarConnector
from src.models.base import get_db
from src.models.lead import Lead
from src.models.session import Session as SessionModel
from src.services.meetings.brief_service import generate_meeting_brief

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


# --- Schemas ---


class MeetingEventSchema(BaseModel):
    """Schema for a scheduled meeting or calendar event."""

    event_id: str = Field(..., description="Unique event identifier or calendar reference ID")
    meeting_id: Optional[str] = Field(None, description="Associated database meeting UUID if present")
    lead_id: Optional[str] = Field(None, description="Associated lead UUID if present")
    title: str = Field(..., description="Title or summary of the meeting")
    start_timestamp: datetime = Field(..., description="Scheduled start timestamp")
    end_timestamp: datetime = Field(..., description="Scheduled end timestamp")
    description: Optional[str] = Field(None, description="Brief description or summary notes")
    location: Optional[str] = Field(None, description="Meeting location or video link URL")
    status: str = Field("CONFIRMED", description="Meeting status string (e.g. CONFIRMED, CANCELLED)")
    is_external: bool = Field(False, description="True if retrieved from external calendar provider")
    is_fallback: bool = Field(False, description="True if retrieved via local database fallback mechanism")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional metadata or provider properties")

    model_config = ConfigDict(from_attributes=True)


class MeetingCreateSchema(BaseModel):
    """Schema for creating or scheduling a new sales meeting."""

    title: str = Field(..., min_length=1, max_length=255, description="Title of the scheduled meeting")
    start_timestamp: datetime = Field(..., description="Scheduled start timestamp")
    end_timestamp: datetime = Field(..., description="Scheduled end timestamp")
    lead_id: Optional[str] = Field(None, description="Optional lead UUID associated with the meeting")
    description: Optional[str] = Field(None, description="Optional meeting description or brief notes")
    location: Optional[str] = Field(None, description="Optional meeting location or video conference link")

    model_config = ConfigDict(from_attributes=True)


class MeetingBriefSchema(BaseModel):
    """Schema for synthesized meeting preparation brief."""

    brief_id: str = Field(..., description="Unique identifier for the meeting brief")
    meeting_id: str = Field(..., description="Associated scheduled meeting UUID")
    lead_id: str = Field(..., description="Associated lead UUID")
    quality_score: int = Field(..., ge=0, le=100, description="Lead quality score (0 to 100)")
    interaction_history_summary: str = Field(..., description="Synthesized background and interaction history summary")
    key_discussion_notes: str = Field(..., description="Synthesized key discussion topics and recommended agenda")
    brief_summary_text: str = Field(..., description="Concise high-level brief summary text")
    created_timestamp: datetime = Field(..., description="Timestamp when the brief was synthesized")

    model_config = ConfigDict(from_attributes=True)


# --- Helper Validation Functions ---


def _validate_date_range(
    start_timestamp: Optional[datetime],
    end_timestamp: Optional[datetime],
) -> None:
    """Validate date/time range query parameters at the API boundary (FR-12, NFR-3)."""
    if start_timestamp and end_timestamp and start_timestamp > end_timestamp:
        logger.warning(
            "Invalid date range parameter: start_timestamp (%s) > end_timestamp (%s)",
            start_timestamp,
            end_timestamp,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_DATE_RANGE",
                "message": "start_timestamp cannot be greater than end_timestamp",
                "details": {
                    "start_timestamp": start_timestamp.isoformat(),
                    "end_timestamp": end_timestamp.isoformat(),
                },
            },
        )


# --- Endpoints ---


@router.get(
    "",
    response_model=List[MeetingEventSchema],
    summary="Get scheduled sales meetings calendar events",
    description="Retrieve scheduled sales meetings with leads, with optional date range and lead filter.",
)
def get_scheduled_meetings(
    start_timestamp: Optional[datetime] = Query(
        None, description="Filter start timestamp for scheduled meetings"
    ),
    end_timestamp: Optional[datetime] = Query(
        None, description="Filter end timestamp for scheduled meetings"
    ),
    lead_id: Optional[str] = Query(
        None, description="Optional filter for specific lead UUID"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> List[MeetingEventSchema]:
    """Expose scheduled meetings calendar view (FR-6, FR-12, NFR-3, NFR-5)."""
    _validate_date_range(start_timestamp, end_timestamp)

    connector = CalendarConnector()
    events = connector.fetch_events(
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        lead_id=lead_id,
        db_session=db,
    )

    return [MeetingEventSchema.model_validate(e.to_dict()) for e in events]


@router.post(
    "",
    response_model=MeetingEventSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a new sales meeting",
    description="Schedule a new meeting with a lead and optional calendar event creation.",
)
def create_scheduled_meeting(
    payload: MeetingCreateSchema,
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> MeetingEventSchema:
    """Schedule a new meeting and optionally synchronize with external calendar provider."""
    _validate_date_range(payload.start_timestamp, payload.end_timestamp)

    if payload.lead_id:
        lead_entity = db.get(Lead, payload.lead_id)
        if not lead_entity:
            logger.warning("Lead with ID '%s' not found for meeting creation.", payload.lead_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LEAD_NOT_FOUND",
                    "message": f"Lead with id '{payload.lead_id}' was not found.",
                    "details": {"lead_id": payload.lead_id},
                },
            )

    connector = CalendarConnector()
    event = connector.create_event(
        title=payload.title,
        start_timestamp=payload.start_timestamp,
        end_timestamp=payload.end_timestamp,
        lead_id=payload.lead_id,
        description=payload.description,
        location=payload.location,
        db_session=db,
    )

    return MeetingEventSchema.model_validate(event.to_dict())


@router.get(
    "/{meeting_id}",
    response_model=MeetingEventSchema,
    summary="Get scheduled meeting or event details by ID",
    description="Retrieve single scheduled meeting or calendar event details by meeting ID or calendar event ID.",
)
def get_scheduled_meeting_by_id(
    meeting_id: str,
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> MeetingEventSchema:
    """Expose single scheduled meeting or event details by ID."""
    connector = CalendarConnector()
    event = connector.get_event_by_id(event_id=meeting_id, db_session=db)

    if not event:
        logger.warning("Scheduled meeting or event with ID '%s' not found.", meeting_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MEETING_NOT_FOUND",
                "message": f"Scheduled meeting or event with id '{meeting_id}' was not found.",
                "details": {"meeting_id": meeting_id},
            },
        )

    return MeetingEventSchema.model_validate(event.to_dict())


@router.get(
    "/{meeting_id}/brief",
    response_model=MeetingBriefSchema,
    summary="Get preparation brief for a scheduled meeting",
    description="Retrieve or generate synthesized meeting preparation brief including lead background, interaction history, and discussion topics.",
)
def get_meeting_preparation_brief(
    meeting_id: str,
    force_refresh: bool = Query(
        False, description="Optional boolean flag to force re-synthesis of brief details"
    ),
    session: SessionModel = Depends(get_current_session),
    db: DBSession = Depends(get_db),
) -> MeetingBriefSchema:
    """Expose synthesized meeting preparation brief for scheduled sales meetings (FR-7, NFR-3, NFR-5)."""
    brief_details = generate_meeting_brief(
        db=db,
        meeting_id=meeting_id,
        force_refresh=force_refresh,
    )

    if not brief_details:
        logger.warning("Brief synthesis requested for unknown meeting_id='%s'", meeting_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MEETING_NOT_FOUND",
                "message": f"Scheduled meeting or lead for brief with id '{meeting_id}' was not found.",
                "details": {"meeting_id": meeting_id},
            },
        )

    return MeetingBriefSchema.model_validate(brief_details.to_dict())
