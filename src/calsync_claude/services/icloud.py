"""iCloud Calendar service implementation with async support."""

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, AsyncIterator
from urllib.parse import urljoin

import caldav
from caldav import DAVClient
import pytz
from dateutil.parser import parse as parse_date
from icalendar import Calendar, Event as ICalEvent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseCalendarService, CalendarServiceError, AuthenticationError, EventNotFoundError
from ..models import CalendarEvent, CalendarInfo, EventSource
from ..config import Settings


class iCloudCalendarService(BaseCalendarService):
    """iCloud Calendar service with async support using CalDAV."""
    
    def __init__(self, settings: Settings):
        """Initialize iCloud Calendar service.
        
        Args:
            settings: Application settings
        """
        super().__init__(settings, EventSource.ICLOUD)
        self.client = None
        self.principal = None
    
    async def authenticate(self) -> None:
        """Authenticate with iCloud CalDAV."""
        try:
            # Run CalDAV connection in executor to avoid blocking
            self.client = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: DAVClient(
                    url=self.settings.icloud_server_url,
                    username=self.settings.icloud_username,
                    password=self.settings.icloud_password
                )
            )
            
            self.principal = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.principal()
            )
            
            self._authenticated = True
            self.logger.info("Successfully authenticated with iCloud CalDAV")
            
        except Exception as e:
            raise AuthenticationError(f"iCloud CalDAV authentication failed: {e}")
    
    async def get_calendars(self) -> List[CalendarInfo]:
        """Get list of iCloud calendars."""
        self._ensure_authenticated()
        
        try:
            # Get calendars from CalDAV
            calendars = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.principal.calendars()
            )
            
            calendar_infos = []
            for i, cal in enumerate(calendars):
                try:
                    # Get calendar properties
                    cal_props = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: cal.get_properties([caldav.dav.DisplayName()])
                    )
                    
                    name = cal_props.get(caldav.dav.DisplayName.tag, f"Calendar {i + 1}")
                    
                    calendar_info = CalendarInfo(
                        id=str(cal.url),
                        name=name,
                        source=EventSource.ICLOUD,
                        is_primary=i == 0,  # First calendar as primary
                        is_selected=str(cal.url) in self.settings.sync_config.selected_icloud_calendars
                        if self.settings.sync_config.selected_icloud_calendars
                        else i == 0  # Select primary by default
                    )
                    calendar_infos.append(calendar_info)
                    
                except Exception as e:
                    self.logger.warning(f"Failed to get properties for calendar {i}: {e}")
                    # Add calendar with minimal info
                    calendar_info = CalendarInfo(
                        id=str(cal.url),
                        name=f"Calendar {i + 1}",
                        source=EventSource.ICLOUD,
                        is_primary=i == 0
                    )
                    calendar_infos.append(calendar_info)
            
            return calendar_infos
            
        except Exception as e:
            raise CalendarServiceError(f"Failed to get iCloud calendars: {e}")
    
    async def get_primary_calendar(self) -> CalendarInfo:
        """Get primary iCloud calendar."""
        calendars = await self.get_calendars()
        
        # Find primary calendar
        for calendar in calendars:
            if calendar.is_primary:
                return calendar
        
        # Fallback to first calendar
        if calendars:
            return calendars[0]
        
        raise CalendarServiceError("No iCloud calendars found")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=8, max=60),  # More aggressive backoff for iCloud
        retry=retry_if_exception_type(CalendarServiceError)
    )
    async def get_events(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
        sync_token: Optional[str] = None,
    ) -> AsyncIterator[CalendarEvent]:
        """Get events from iCloud calendar asynchronously."""
        self._ensure_authenticated()
        
        # Set default time range if not specified
        if time_min is None:
            time_min = datetime.now(pytz.UTC) - timedelta(
                days=self.settings.sync_config.sync_past_days
            )
        if time_max is None:
            time_max = datetime.now(pytz.UTC) + timedelta(
                days=self.settings.sync_config.sync_future_days
            )
        
        try:
            # Find the calendar by ID
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")
            
            # Get events from CalDAV - use sync_token if available for incremental sync
            try:
                if sync_token:
                    # TODO: Implement proper CalDAV sync-collection support
                    # For now, fall back to date search with ctag comparison
                    self.logger.warning("iCloud sync tokens not yet implemented, using date search")
                    events = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: calendar.date_search(start=time_min, end=time_max)
                    )
                else:
                    events = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: calendar.date_search(start=time_min, end=time_max)
                    )
            except Exception as e:
                if "429" in str(e) or "throttl" in str(e).lower():
                    self.logger.warning("iCloud CalDAV throttled, retrying with backoff...")
                    raise CalendarServiceError(f"iCloud throttled: {e}")
                raise CalendarServiceError(f"Failed to get iCloud events: {e}")
            
            events_yielded = 0
            for event in events:
                if max_results and events_yielded >= max_results:
                    break
                
                try:
                    formatted_event = self._parse_caldav_event(event)
                    if formatted_event:
                        # Filter by updated_min if specified
                        if updated_min and formatted_event.updated < updated_min:
                            continue
                        
                        yield formatted_event
                        events_yielded += 1
                        
                except Exception as e:
                    self.logger.warning(f"Failed to parse iCloud event: {e}")
                    continue
                    
        except Exception as e:
            raise CalendarServiceError(f"Failed to get iCloud events: {e}")
    
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        """Get a specific iCloud Calendar event."""
        self._ensure_authenticated()
        
        try:
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")
            
            # Search for event by UID
            events = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )
            
            for event in events:
                try:
                    parsed_event = self._parse_caldav_event(event)
                    if parsed_event and parsed_event.id == event_id:
                        return parsed_event
                except Exception:
                    continue
            
            raise EventNotFoundError(f"iCloud event {event_id} not found")
            
        except EventNotFoundError:
            raise
        except Exception as e:
            raise CalendarServiceError(f"Failed to get iCloud event: {e}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=8, max=60),
        retry=retry_if_exception_type(CalendarServiceError)
    )
    async def create_event(
        self,
        calendar_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Create a new iCloud Calendar event."""
        self._ensure_authenticated()
        
        try:
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")
            
            # Create iCal data
            ical_data = self._create_ical_event(event_data)
            
            # Create event
            created_event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.save_event(ical_data)
            )
            
            return self._parse_caldav_event(created_event)
            
        except Exception as e:
            raise CalendarServiceError(f"Failed to create iCloud event: {e}")
    
    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Update an iCloud Calendar event."""
        self._ensure_authenticated()
        
        try:
            # Find the event first
            existing_event = await self.get_event(calendar_id, event_id)
            
            # Find the CalDAV event object
            calendar = await self._find_calendar_by_id(calendar_id)
            events = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )
            
            caldav_event = None
            for event in events:
                try:
                    if self._extract_uid_from_caldav_event(event) == event_id:
                        caldav_event = event
                        break
                except Exception:
                    continue
            
            if not caldav_event:
                raise EventNotFoundError(f"iCloud event {event_id} not found")
            
            # Update the event
            ical_data = self._create_ical_event(event_data)
            
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: setattr(caldav_event, 'data', ical_data) or caldav_event.save()
            )
            
            return self._parse_caldav_event(caldav_event)
            
        except EventNotFoundError:
            raise
        except Exception as e:
            raise CalendarServiceError(f"Failed to update iCloud event: {e}")
    
    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an iCloud Calendar event."""
        self._ensure_authenticated()
        
        try:
            # Find the CalDAV event object
            calendar = await self._find_calendar_by_id(calendar_id)
            events = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )
            
            for event in events:
                try:
                    if self._extract_uid_from_caldav_event(event) == event_id:
                        await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: event.delete()
                        )
                        return
                except Exception:
                    continue
            
            raise EventNotFoundError(f"iCloud event {event_id} not found")
            
        except EventNotFoundError:
            raise
        except Exception as e:
            raise CalendarServiceError(f"Failed to delete iCloud event: {e}")
    
    async def _find_calendar_by_id(self, calendar_id: str):
        """Find calendar object by ID."""
        calendars = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.principal.calendars()
        )
        
        for calendar in calendars:
            if str(calendar.url) == calendar_id:
                return calendar
        return None
    
    def _parse_caldav_event(self, event) -> Optional[CalendarEvent]:
        """Parse CalDAV event to standardized format using proper iCal parser."""
        try:
            # Parse the iCal data with icalendar library
            cal = Calendar.from_ical(event.data)
            
            # Find the VEVENT component
            vevent = None
            for component in cal.walk():
                if component.name == "VEVENT":
                    vevent = component
                    break
            
            if not vevent:
                return None
            
            # Extract basic fields
            summary = str(vevent.get('summary', ''))
            description = str(vevent.get('description', ''))
            location = str(vevent.get('location', ''))
            
            # Extract UID
            uid = str(vevent.get('uid', str(hash(event.data))))
            
            # Parse dates with proper timezone handling
            dtstart = vevent.get('dtstart')
            dtend = vevent.get('dtend')
            
            if not dtstart:
                return None
            
            start_dt = dtstart.dt
            all_day = not isinstance(start_dt, datetime)
            
            # Handle timezone extraction
            timezone = None
            if not all_day and hasattr(start_dt, 'tzinfo') and start_dt.tzinfo:
                timezone = str(start_dt.tzinfo)
            
            # Convert to datetime and handle all-day events
            if all_day:
                # Keep date format for all-day events
                start_dt = datetime.combine(start_dt, datetime.min.time())
                if dtend:
                    end_dt = datetime.combine(dtend.dt, datetime.min.time())
                else:
                    end_dt = start_dt + timedelta(days=1)
            else:
                # Ensure timezone-aware datetimes
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=pytz.UTC)
                if dtend:
                    end_dt = dtend.dt
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=pytz.UTC)
                else:
                    end_dt = start_dt + timedelta(hours=1)
            
            # Parse timestamps
            created_prop = vevent.get('created')
            created_dt = created_prop.dt if created_prop else datetime.now(pytz.UTC)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=pytz.UTC)
            
            last_modified_prop = vevent.get('last-modified')
            updated_dt = last_modified_prop.dt if last_modified_prop else created_dt
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=pytz.UTC)
            
            # Extract sequence for conflict resolution
            sequence = int(vevent.get('sequence', 0))
            
            # Extract recurrence information
            rrule = vevent.get('rrule')
            recurrence_rule = str(rrule) if rrule else None
            
            # Extract recurrence overrides (RDATE, EXDATE)
            recurrence_overrides = []
            for prop in ['rdate', 'exdate']:
                if prop in vevent:
                    recurrence_overrides.append({
                        'type': prop,
                        'dates': [str(d) for d in vevent[prop].to_ical().decode().split(',')]
                    })
            
            # Extract resource URL for direct access (CRITICAL for production)
            resource_url = str(event.url) if hasattr(event, 'url') and event.url else None
            
            return CalendarEvent(
                id=uid,
                uid=uid,
                source=EventSource.ICLOUD,
                summary=summary,
                description=description,
                location=location,
                start=start_dt,
                end=end_dt,
                all_day=all_day,
                timezone=timezone,
                created=created_dt,
                updated=updated_dt,
                sequence=sequence,
                recurrence_rule=recurrence_rule,
                recurrence_overrides=recurrence_overrides,
                original_data={
                    'caldav_event': event, 
                    'ical_data': event.data, 
                    'vevent': vevent,
                    'resource_url': resource_url  # Store for direct access
                }
            )
            
        except Exception as e:
            self.logger.warning(f"Error parsing CalDAV event: {e}")
            return None
    
    
    def _extract_uid_from_caldav_event(self, event) -> str:
        """Extract UID from CalDAV event."""
        try:
            ical_data = event.data
            uid = self._extract_ical_field(ical_data, 'UID')
            return uid or str(hash(str(event)))
        except:
            return str(hash(str(event)))
    
    def _create_ical_event(self, event_data: CalendarEvent) -> str:
        """Create iCal format string from event data using proper iCal library."""
        # Create calendar and event components
        cal = Calendar()
        cal.add('prodid', '-//CalSync Claude//CalSync Claude 2.0//EN')
        cal.add('version', '2.0')
        
        event = ICalEvent()
        
        # Use UID from event or generate one
        uid = event_data.uid or event_data.id or f"calsync-claude-{hash(str(event_data))}"
        event.add('uid', uid)
        
        # Add basic properties
        event.add('summary', event_data.summary)
        if event_data.description:
            event.add('description', event_data.description)
        if event_data.location:
            event.add('location', event_data.location)
        
        # Handle date/time properly
        if event_data.all_day:
            # All-day events use DATE format
            event.add('dtstart', event_data.start.date())
            event.add('dtend', event_data.end.date())
        else:
            # Timed events with timezone preservation
            if event_data.timezone:
                # Try to preserve original timezone
                try:
                    tz = pytz.timezone(event_data.timezone)
                    start_local = event_data.start.astimezone(tz)
                    end_local = event_data.end.astimezone(tz)
                    event.add('dtstart', start_local)
                    event.add('dtend', end_local)
                except:
                    # Fallback to UTC
                    event.add('dtstart', event_data.start)
                    event.add('dtend', event_data.end)
            else:
                event.add('dtstart', event_data.start)
                event.add('dtend', event_data.end)
        
        # Add timestamps
        now = datetime.now(pytz.UTC)
        event.add('dtstamp', now)
        event.add('created', event_data.created)
        event.add('last-modified', now)
        
        # Add sequence for conflict resolution
        if event_data.sequence is not None:
            event.add('sequence', event_data.sequence)
        
        # Add recurrence rule if present
        if event_data.recurrence_rule:
            try:
                # Parse and add RRULE
                from icalendar.parser import from_ical
                rrule = from_ical(event_data.recurrence_rule)
                event.add('rrule', rrule)
            except:
                # If parsing fails, add as text
                self.logger.warning(f"Failed to parse RRULE: {event_data.recurrence_rule}")
        
        # Add recurrence overrides
        for override in event_data.recurrence_overrides:
            if override['type'] in ['rdate', 'exdate']:
                for date_str in override['dates']:
                    try:
                        date_val = parse_date(date_str)
                        event.add(override['type'], date_val)
                    except:
                        continue
        
        cal.add_component(event)
        return cal.to_ical().decode('utf-8')