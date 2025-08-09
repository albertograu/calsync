"""Base calendar service interface with async support."""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, AsyncIterator
import logging

from ..models import CalendarEvent, CalendarInfo, EventSource, ChangeSet
from ..config import Settings

logger = logging.getLogger(__name__)


class CalendarServiceError(Exception):
    """Base exception for calendar service errors."""
    pass


class AuthenticationError(CalendarServiceError):
    """Authentication-related errors."""
    pass


class RateLimitError(CalendarServiceError):
    """Rate limiting errors."""
    pass


class CalendarNotFoundError(CalendarServiceError):
    """Calendar not found errors."""
    pass


class EventNotFoundError(CalendarServiceError):
    """Event not found errors."""
    pass


class BaseCalendarService(ABC):
    """Abstract base class for calendar services with async support."""
    
    def __init__(self, settings: Settings, source: EventSource):
        """Initialize calendar service.
        
        Args:
            settings: Application settings
            source: Event source type
        """
        self.settings = settings
        self.source = source
        self.logger = logger.getChild(source.value)
        self._authenticated = False
        self._rate_limiter = asyncio.Semaphore(
            settings.rate_limit_requests_per_minute // 60
        )
    
    @abstractmethod
    async def authenticate(self) -> None:
        """Authenticate with the calendar service.
        
        Raises:
            AuthenticationError: If authentication fails
        """
        pass
    
    @abstractmethod
    async def get_calendars(self) -> List[CalendarInfo]:
        """Get list of available calendars.
        
        Returns:
            List of calendar information objects
            
        Raises:
            CalendarServiceError: If calendars cannot be retrieved
        """
        pass
    
    @abstractmethod
    async def get_primary_calendar(self) -> CalendarInfo:
        """Get the primary calendar.
        
        Returns:
            Primary calendar information
            
        Raises:
            CalendarNotFoundError: If no primary calendar found
        """
        pass
    
    @abstractmethod
    async def get_events(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
    ) -> AsyncIterator[CalendarEvent]:
        """Get events from a calendar asynchronously.
        
        Args:
            calendar_id: Calendar ID
            time_min: Minimum event time
            time_max: Maximum event time
            max_results: Maximum number of results
            updated_min: Filter events updated after this time
            
        Yields:
            CalendarEvent objects
            
        Raises:
            CalendarServiceError: If events cannot be retrieved
        """
        pass

    @abstractmethod
    async def get_change_set(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
        sync_token: Optional[str] = None,
    ) -> ChangeSet:
        """Get an incremental change set from a calendar.
        
        Args:
            calendar_id: Calendar ID
            time_min: Optional start time for initial backfill
            time_max: Optional end time for initial backfill
            max_results: Optional cap
            updated_min: Optional filter for initial backfill
            sync_token: If present, do a true incremental sync returning only changes and deletions
        
        Returns:
            ChangeSet containing changed events, deleted IDs, and next sync token when available
        """
        pass
    
    @abstractmethod
    async def create_event(
        self,
        calendar_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Create a new event.
        
        Args:
            calendar_id: Calendar ID
            event_data: Event data to create
            
        Returns:
            Created event
            
        Raises:
            CalendarServiceError: If event cannot be created
        """
        pass
    
    @abstractmethod
    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Update an existing event.
        
        Args:
            calendar_id: Calendar ID
            event_id: Event ID to update
            event_data: Updated event data
            
        Returns:
            Updated event
            
        Raises:
            EventNotFoundError: If event not found
            CalendarServiceError: If event cannot be updated
        """
        pass
    
    @abstractmethod
    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event.
        
        Args:
            calendar_id: Calendar ID
            event_id: Event ID to delete
            
        Raises:
            EventNotFoundError: If event not found
            CalendarServiceError: If event cannot be deleted
        """
        pass
    
    @abstractmethod
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        """Get a specific event by ID.
        
        Args:
            calendar_id: Calendar ID
            event_id: Event ID
            
        Returns:
            Event object
            
        Raises:
            EventNotFoundError: If event not found
            CalendarServiceError: If event cannot be retrieved
        """
        pass
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test connection to the calendar service.
        
        Returns:
            Dictionary with connection test results
        """
        try:
            await self.authenticate()
            calendars = await self.get_calendars()
            
            # Try to get a few events from the primary calendar
            if calendars:
                primary = calendars[0]
                event_count = 0
                async for _ in self.get_events(primary.id, max_results=5):
                    event_count += 1
                
                return {
                    'success': True,
                    'calendar_count': len(calendars),
                    'sample_events': event_count,
                    'primary_calendar': primary.name
                }
            else:
                return {
                    'success': True,
                    'calendar_count': 0,
                    'sample_events': 0,
                    'primary_calendar': None
                }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'error_type': type(e).__name__
            }
    
    async def _rate_limited_request(self, coro):
        """Execute a coroutine with rate limiting.
        
        Args:
            coro: Coroutine to execute
            
        Returns:
            Coroutine result
        """
        async with self._rate_limiter:
            return await coro
    
    def _ensure_authenticated(self):
        """Ensure the service is authenticated.
        
        Raises:
            AuthenticationError: If not authenticated
        """
        if not self._authenticated:
            raise AuthenticationError("Service not authenticated")
    
    async def health_check(self) -> bool:
        """Perform a health check on the service.
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            result = await self.test_connection()
            return result['success']
        except Exception:
            return False