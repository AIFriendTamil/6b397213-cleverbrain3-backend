"""Abstract domain interface port and data structures for external calendar operations."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session


class CalendarError(Exception):
    """Base exception class for calendar port operations."""

    pass


class CalendarAdapterUnavailableError(CalendarError):
    """Raised when external calendar API or service provider is unreachable."""

    pass


class CalendarEventNotFoundError(CalendarError):
    """Raised when a requested calendar event is not found."""

    pass


@dataclass
class CalendarEvent:
    """Dataclass encapsulating a scheduled calendar event or sales meeting."""

    event_id: str
    title: str
    start_timestamp: datetime
    end_timestamp: datetime
    meeting_id: Optional[str] = None
    lead_id: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    status: str = "CONFIRMED"
    is_external: bool = False
    is_fallback: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert calendar event instance to a serializable dictionary."""
        data = asdict(self)
        if isinstance(self.start_timestamp, datetime):
            data["start_timestamp"] = self.start_timestamp.isoformat()
        if isinstance(self.end_timestamp, datetime):
            data["end_timestamp"] = self.end_timestamp.isoformat()
        return data


class CalendarPort(ABC):
    """Abstract domain port defining operations for calendar synchronization and events."""

    @abstractmethod
    def fetch_events(
        self,
        start_timestamp: Optional[datetime] = None,
        end_timestamp: Optional[datetime] = None,
        lead_id: Optional[str] = None,
        db_session: Optional[Session] = None,
    ) -> List[CalendarEvent]:
        """Fetch scheduled meetings or calendar events within an optional time range.

        Args:
            start_timestamp: Optional range start filter timestamp.
            end_timestamp: Optional range end filter timestamp.
            lead_id: Optional filter for a specific lead identifier.
            db_session: Optional database session for local storage query or fallback.

        Returns:
            List of CalendarEvent items.
        """
        pass

    @abstractmethod
    def get_event_by_id(
        self,
        event_id: str,
        db_session: Optional[Session] = None,
    ) -> Optional[CalendarEvent]:
        """Retrieve a single calendar event or meeting by identifier.

        Args:
            event_id: Unique event ID or calendar reference ID.
            db_session: Optional database session for local storage query or fallback.

        Returns:
            CalendarEvent instance if found, or None.
        """
        pass

    @abstractmethod
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
            description: Optional meeting description or brief notes.
            location: Optional venue or meeting link location string.
            db_session: Optional database session for persisting local meeting.

        Returns:
            Created CalendarEvent instance.
        """
        pass

    @abstractmethod
    def sync_events(
        self,
        db_session: Session,
        start_timestamp: Optional[datetime] = None,
        end_timestamp: Optional[datetime] = None,
    ) -> List[CalendarEvent]:
        """Synchronize external calendar events with local database storage.

        Args:
            db_session: Synchronous database session for persistence.
            start_timestamp: Optional lower boundary timestamp.
            end_timestamp: Optional upper boundary timestamp.

        Returns:
            List of synchronized CalendarEvent items.
        """
        pass
