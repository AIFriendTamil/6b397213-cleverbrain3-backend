"""External calendar integration connector implementing CalendarPort with local database fallback."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.meeting import ScheduledMeeting
from src.services.meetings.calendar_port import (
    CalendarAdapterUnavailableError,
    CalendarError,
    CalendarEvent,
    CalendarEventNotFoundError,
    CalendarPort,
)

logger = logging.getLogger(__name__)


def parse_timestamp_iso(ts_value: Any) -> datetime:
    """Parse string or datetime input into an ISO-aware datetime object.

    Args:
        ts_value: Datetime object or ISO string.

    Returns:
        timezone-aware datetime instance.
    """
    if isinstance(ts_value, datetime):
        if ts_value.tzinfo is None:
            return ts_value.replace(tzinfo=timezone.utc)
        return ts_value
    if isinstance(ts_value, str):
        cleaned_str = ts_value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.now(timezone.utc)


class CalendarConnector(CalendarPort):
    """External calendar connector adapter supporting remote API calls and local fallback."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        fallback_to_local: Optional[bool] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """Initialize calendar connector with configuration and optional HTTP client override.

        Args:
            api_url: Base endpoint URL for external calendar API.
            api_key: Optional secret authentication token or API key.
            provider: Provider mode string ('external', 'mock', 'local').
            fallback_to_local: Whether to fallback to local database on external API failure.
            timeout_seconds: Request timeout duration in seconds.
            http_client: Optional pre-configured httpx Client instance for HTTP calls.
        """
        self.api_url = (api_url or settings.CALENDAR_API_URL).rstrip("/")
        self.api_key = api_key or settings.CALENDAR_API_KEY
        self.provider = (provider or settings.CALENDAR_PROVIDER).lower()
        self.fallback_to_local = (
            settings.CALENDAR_FALLBACK_TO_LOCAL
            if fallback_to_local is None
            else fallback_to_local
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(settings.CALENDAR_API_TIMEOUT_SECONDS)
        )
        self._custom_client = http_client

    def _get_headers(self) -> Dict[str, str]:
        """Construct HTTP headers required for external calendar API authorization."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def _fetch_local_events(
        self,
        db_session: Optional[Session],
        start_timestamp: Optional[datetime] = None,
        end_timestamp: Optional[datetime] = None,
        lead_id: Optional[str] = None,
        is_fallback: bool = True,
    ) -> List[CalendarEvent]:
        """Fetch scheduled sales meetings from local database storage.

        Args:
            db_session: Synchronous SQLAlchemy database session.
            start_timestamp: Optional lower bound timestamp filter.
            end_timestamp: Optional upper bound timestamp filter.
            lead_id: Optional lead identifier filter.
            is_fallback: Boolean flag indicating if result is from a fallback trigger.

        Returns:
            List of CalendarEvent items generated from local ScheduledMeeting entities.
        """
        if not db_session:
            logger.info("No db_session provided for local meeting lookup; returning empty list.")
            return []

        stmt = select(ScheduledMeeting)
        if start_timestamp is not None:
            stmt = stmt.where(ScheduledMeeting.start_timestamp >= start_timestamp)
        if end_timestamp is not None:
            stmt = stmt.where(ScheduledMeeting.end_timestamp <= end_timestamp)
        if lead_id is not None:
            stmt = stmt.where(ScheduledMeeting.lead_id == lead_id)

        stmt = stmt.order_by(ScheduledMeeting.start_timestamp.asc())

        meetings = db_session.scalars(stmt).all()
        events = []
        for m in meetings:
            event_id = m.calendar_event_id or m.meeting_id
            events.append(
                CalendarEvent(
                    event_id=event_id,
                    meeting_id=m.meeting_id,
                    lead_id=m.lead_id,
                    title=m.title_text,
                    start_timestamp=m.start_timestamp,
                    end_timestamp=m.end_timestamp,
                    description=m.brief_summary_text,
                    status="CONFIRMED",
                    is_external=False,
                    is_fallback=is_fallback,
                    details={"source": "local_database"},
                )
            )
        return events

    def fetch_events(
        self,
        start_timestamp: Optional[datetime] = None,
        end_timestamp: Optional[datetime] = None,
        lead_id: Optional[str] = None,
        db_session: Optional[Session] = None,
    ) -> List[CalendarEvent]:
        """Fetch scheduled meetings or calendar events within an optional time range.

        If external provider call fails or is configured in mock mode, gracefully falls
        back to querying local stored meetings.

        Args:
            start_timestamp: Optional range start filter timestamp.
            end_timestamp: Optional range end filter timestamp.
            lead_id: Optional lead UUID filter.
            db_session: Optional database session for local query fallback.

        Returns:
            List of CalendarEvent instances.

        Raises:
            CalendarAdapterUnavailableError: If external calendar API fails and fallback is disabled.
        """
        if self.provider in ("mock", "local"):
            logger.info("Calendar connector operating in '%s' mode, fetching local events.", self.provider)
            return self._fetch_local_events(
                db_session=db_session,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                lead_id=lead_id,
                is_fallback=False,
            )

        # External provider mode
        params: Dict[str, str] = {}
        if start_timestamp:
            params["start_timestamp"] = start_timestamp.isoformat()
        if end_timestamp:
            params["end_timestamp"] = end_timestamp.isoformat()
        if lead_id:
            params["lead_id"] = lead_id

        try:
            url = f"{self.api_url}/events"
            headers = self._get_headers()

            if self._custom_client is not None:
                response = self._custom_client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.get(
                        url,
                        params=params,
                        headers=headers,
                    )

            if response.status_code >= 400:
                raise CalendarAdapterUnavailableError(
                    f"External calendar API HTTP {response.status_code}: {response.text}"
                )

            data = response.json()
            raw_events = data if isinstance(data, list) else data.get("events", [])

            events = []
            for item in raw_events:
                events.append(
                    CalendarEvent(
                        event_id=item.get("event_id") or item.get("id") or str(uuid.uuid4()),
                        meeting_id=item.get("meeting_id"),
                        lead_id=item.get("lead_id"),
                        title=item.get("title") or item.get("summary") or "Scheduled Meeting",
                        start_timestamp=parse_timestamp_iso(
                            item.get("start_timestamp") or item.get("start")
                        ),
                        end_timestamp=parse_timestamp_iso(
                            item.get("end_timestamp") or item.get("end")
                        ),
                        description=item.get("description"),
                        location=item.get("location"),
                        status=item.get("status") or "CONFIRMED",
                        is_external=True,
                        is_fallback=False,
                        details=item.get("details", {}),
                    )
                )
            return events

        except Exception as exc:
            logger.warning(
                "External calendar API fetch failed (%s); fallback_to_local=%s",
                exc,
                self.fallback_to_local,
            )
            if self.fallback_to_local:
                return self._fetch_local_events(
                    db_session=db_session,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                    lead_id=lead_id,
                    is_fallback=True,
                )
            raise CalendarAdapterUnavailableError(
                f"External calendar API unavailable: {exc}"
            ) from exc

    def get_event_by_id(
        self,
        event_id: str,
        db_session: Optional[Session] = None,
    ) -> Optional[CalendarEvent]:
        """Retrieve a single calendar event or meeting by identifier.

        Args:
            event_id: Unique event ID or calendar reference ID.
            db_session: Optional database session for local lookup or fallback.

        Returns:
            CalendarEvent instance if found, or None.

        Raises:
            CalendarAdapterUnavailableError: If external calendar API fails and fallback disabled.
        """
        if self.provider in ("mock", "local"):
            if not db_session:
                return None
            stmt = select(ScheduledMeeting).where(
                or_(
                    ScheduledMeeting.calendar_event_id == event_id,
                    ScheduledMeeting.meeting_id == event_id,
                )
            )
            meeting = db_session.scalar(stmt)
            if not meeting:
                return None
            return CalendarEvent(
                event_id=meeting.calendar_event_id or meeting.meeting_id,
                meeting_id=meeting.meeting_id,
                lead_id=meeting.lead_id,
                title=meeting.title_text,
                start_timestamp=meeting.start_timestamp,
                end_timestamp=meeting.end_timestamp,
                description=meeting.brief_summary_text,
                status="CONFIRMED",
                is_external=False,
                is_fallback=False,
            )

        try:
            url = f"{self.api_url}/events/{event_id}"
            headers = self._get_headers()

            if self._custom_client is not None:
                response = self._custom_client.get(
                    url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.get(
                        url,
                        headers=headers,
                    )

            if response.status_code == 404:
                # Try local lookup before returning None
                if self.fallback_to_local and db_session:
                    return self.get_event_by_id_local(event_id, db_session, is_fallback=True)
                return None

            if response.status_code >= 400:
                raise CalendarAdapterUnavailableError(
                    f"External calendar API HTTP {response.status_code}: {response.text}"
                )

            item = response.json()
            return CalendarEvent(
                event_id=item.get("event_id") or item.get("id") or event_id,
                meeting_id=item.get("meeting_id"),
                lead_id=item.get("lead_id"),
                title=item.get("title") or item.get("summary") or "Scheduled Meeting",
                start_timestamp=parse_timestamp_iso(
                    item.get("start_timestamp") or item.get("start")
                ),
                end_timestamp=parse_timestamp_iso(
                    item.get("end_timestamp") or item.get("end")
                ),
                description=item.get("description"),
                location=item.get("location"),
                status=item.get("status") or "CONFIRMED",
                is_external=True,
                is_fallback=False,
                details=item.get("details", {}),
            )
        except Exception as exc:
            logger.warning("External calendar API get_event_by_id failed (%s)", exc)
            if self.fallback_to_local and db_session:
                return self.get_event_by_id_local(event_id, db_session, is_fallback=True)
            if self.fallback_to_local:
                return None
            raise CalendarAdapterUnavailableError(
                f"External calendar API unavailable: {exc}"
            ) from exc

    def get_event_by_id_local(
        self,
        event_id: str,
        db_session: Session,
        is_fallback: bool = True,
    ) -> Optional[CalendarEvent]:
        """Lookup a calendar event in local database by event_id or meeting_id.

        Args:
            event_id: External event identifier or meeting UUID.
            db_session: Synchronous database session.
            is_fallback: Flag indicating whether this lookup is a fallback trigger.

        Returns:
            CalendarEvent if found in local ScheduledMeeting model, else None.
        """
        stmt = select(ScheduledMeeting).where(
            or_(
                ScheduledMeeting.calendar_event_id == event_id,
                ScheduledMeeting.meeting_id == event_id,
            )
        )
        meeting = db_session.scalar(stmt)
        if not meeting:
            return None
        return CalendarEvent(
            event_id=meeting.calendar_event_id or meeting.meeting_id,
            meeting_id=meeting.meeting_id,
            lead_id=meeting.lead_id,
            title=meeting.title_text,
            start_timestamp=meeting.start_timestamp,
            end_timestamp=meeting.end_timestamp,
            description=meeting.brief_summary_text,
            status="CONFIRMED",
            is_external=False,
            is_fallback=is_fallback,
        )

    def create_event(
        self,
        title: str,
        start_timestamp: datetime,
        end_timestamp: datetime,
        lead_id: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        db_session: Optional[Session] = None,
    ) -> CalendarEvent:
        """Create or schedule a new calendar event.

        Args:
            title: Title text for the event.
            start_timestamp: Scheduled start timestamp.
            end_timestamp: Scheduled end timestamp.
            lead_id: Optional associated lead UUID.
            description: Optional meeting description.
            location: Optional venue or location.
            db_session: Optional database session for local persistence.

        Returns:
            CalendarEvent instance created.
        """
        start_dt = parse_timestamp_iso(start_timestamp)
        end_dt = parse_timestamp_iso(end_timestamp)

        meeting_record: Optional[ScheduledMeeting] = None
        if db_session and lead_id:
            meeting_record = ScheduledMeeting(
                lead_id=lead_id,
                title_text=title,
                start_timestamp=start_dt,
                end_timestamp=end_dt,
                brief_summary_text=description,
            )
            db_session.add(meeting_record)
            db_session.commit()
            db_session.refresh(meeting_record)

        if self.provider in ("mock", "local"):
            event_id = (
                meeting_record.calendar_event_id
                if meeting_record and meeting_record.calendar_event_id
                else (meeting_record.meeting_id if meeting_record else str(uuid.uuid4()))
            )
            return CalendarEvent(
                event_id=event_id,
                meeting_id=meeting_record.meeting_id if meeting_record else None,
                lead_id=lead_id,
                title=title,
                start_timestamp=start_dt,
                end_timestamp=end_dt,
                description=description,
                location=location,
                status="CONFIRMED",
                is_external=False,
                is_fallback=False,
            )

        payload = {
            "title": title,
            "start_timestamp": start_dt.isoformat(),
            "end_timestamp": end_dt.isoformat(),
            "lead_id": lead_id,
            "description": description,
            "location": location,
        }

        try:
            url = f"{self.api_url}/events"
            headers = self._get_headers()

            if self._custom_client is not None:
                response = self._custom_client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        url,
                        json=payload,
                        headers=headers,
                    )

            if response.status_code >= 400:
                raise CalendarAdapterUnavailableError(
                    f"External calendar API HTTP {response.status_code}: {response.text}"
                )

            item = response.json()
            ext_event_id = item.get("event_id") or item.get("id") or str(uuid.uuid4())

            if meeting_record and db_session:
                meeting_record.calendar_event_id = ext_event_id
                db_session.commit()

            return CalendarEvent(
                event_id=ext_event_id,
                meeting_id=meeting_record.meeting_id if meeting_record else item.get("meeting_id"),
                lead_id=lead_id,
                title=title,
                start_timestamp=start_dt,
                end_timestamp=end_dt,
                description=description,
                location=location,
                status="CONFIRMED",
                is_external=True,
                is_fallback=False,
                details=item.get("details", {}),
            )
        except Exception as exc:
            logger.warning("External calendar event creation failed (%s)", exc)
            if self.fallback_to_local:
                event_id = (
                    meeting_record.meeting_id if meeting_record else str(uuid.uuid4())
                )
                return CalendarEvent(
                    event_id=event_id,
                    meeting_id=meeting_record.meeting_id if meeting_record else None,
                    lead_id=lead_id,
                    title=title,
                    start_timestamp=start_dt,
                    end_timestamp=end_dt,
                    description=description,
                    location=location,
                    status="CONFIRMED",
                    is_external=False,
                    is_fallback=True,
                )
            raise CalendarAdapterUnavailableError(
                f"External calendar API unavailable: {exc}"
            ) from exc

    def sync_events(
        self,
        db_session: Session,
        start_timestamp: Optional[datetime] = None,
        end_timestamp: Optional[datetime] = None,
    ) -> List[CalendarEvent]:
        """Synchronize external calendar events with local database storage.

        Args:
            db_session: Synchronous database session for persistence.
            start_timestamp: Optional lower boundary timestamp filter.
            end_timestamp: Optional upper boundary timestamp filter.

        Returns:
            List of synchronized CalendarEvent items.
        """
        fetched = self.fetch_events(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            db_session=db_session,
        )

        for event in fetched:
            if event.lead_id:
                stmt = select(ScheduledMeeting).where(
                    or_(
                        ScheduledMeeting.calendar_event_id == event.event_id,
                        ScheduledMeeting.meeting_id == (event.meeting_id or event.event_id),
                    )
                )
                existing = db_session.scalar(stmt)
                if not existing:
                    new_meeting = ScheduledMeeting(
                        lead_id=event.lead_id,
                        title_text=event.title,
                        start_timestamp=event.start_timestamp,
                        end_timestamp=event.end_timestamp,
                        calendar_event_id=event.event_id,
                        brief_summary_text=event.description,
                    )
                    db_session.add(new_meeting)
                else:
                    existing.title_text = event.title
                    existing.start_timestamp = event.start_timestamp
                    existing.end_timestamp = event.end_timestamp
                    if event.event_id:
                        existing.calendar_event_id = event.event_id

        db_session.commit()
        return fetched


ExternalCalendarConnector = CalendarConnector
