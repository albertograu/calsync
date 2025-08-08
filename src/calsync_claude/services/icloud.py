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
    
    async def get_events(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
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
            
            # Get events from CalDAV
            events = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.date_search(start=time_min, end=time_max)
            )
            
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
        """Parse CalDAV event to standardized format."""
        try:
            # Get the iCal data
            ical_data = event.data
            
            # Parse basic fields using regex
            summary = self._extract_ical_field(ical_data, 'SUMMARY') or ''
            description = self._extract_ical_field(ical_data, 'DESCRIPTION') or ''
            location = self._extract_ical_field(ical_data, 'LOCATION') or ''
            
            # Parse dates
            dtstart = self._extract_ical_field(ical_data, 'DTSTART')
            dtend = self._extract_ical_field(ical_data, 'DTEND')
            created = self._extract_ical_field(ical_data, 'CREATED')
            last_modified = self._extract_ical_field(ical_data, 'LAST-MODIFIED')
            
            if not dtstart:
                return None
            
            # Parse start and end times
            start_dt, all_day_start = self._parse_ical_datetime(dtstart)
            end_dt, all_day_end = self._parse_ical_datetime(dtend) if dtend else (None, False)
            
            # If no end time, assume duration
            if not end_dt:
                if all_day_start:
                    end_dt = start_dt + timedelta(days=1)
                else:
                    end_dt = start_dt + timedelta(hours=1)
            
            all_day = all_day_start or all_day_end
            
            # Parse timestamps
            created_dt = self._parse_ical_datetime(created)[0] if created else datetime.now(pytz.UTC)
            updated_dt = self._parse_ical_datetime(last_modified)[0] if last_modified else created_dt
            
            # Extract UID
            uid = self._extract_ical_field(ical_data, 'UID') or str(hash(ical_data))
            
            return CalendarEvent(
                id=uid,
                source=EventSource.ICLOUD,
                summary=summary,
                description=description,
                location=location,
                start=start_dt,
                end=end_dt,
                all_day=all_day,
                created=created_dt,
                updated=updated_dt,
                original_data={'caldav_event': event, 'ical_data': ical_data}
            )
            
        except Exception as e:
            self.logger.warning(f"Error parsing CalDAV event: {e}")
            return None
    
    def _extract_ical_field(self, ical_data: str, field_name: str) -> Optional[str]:
        """Extract a field value from iCal data."""
        # Handle multi-line folding in iCal format
        unfolded_data = re.sub(r'\r?\n[ \t]', '', ical_data)
        
        # Look for the field
        pattern = rf'^{field_name}[^:]*:(.*)$'
        match = re.search(pattern, unfolded_data, re.MULTILINE | re.IGNORECASE)
        
        if match:
            value = match.group(1).strip()
            # Unescape common iCal escapes
            value = value.replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';')
            return value
        
        return None
    
    def _parse_ical_datetime(self, dt_string: str) -> tuple[datetime, bool]:
        """Parse iCal datetime string."""
        if not dt_string:
            return datetime.now(pytz.UTC), False
        
        # Remove VALUE=DATE if present
        dt_string = re.sub(r'VALUE=DATE[^:]*:', '', dt_string)
        
        # Check if it's a date-only (all-day) event
        if re.match(r'^\d{8}$', dt_string):
            # YYYYMMDD format (all-day)
            dt = datetime.strptime(dt_string, '%Y%m%d')
            return dt.replace(tzinfo=pytz.UTC), True
        
        # Try to parse as datetime
        try:
            # Remove timezone info for parsing
            clean_dt = re.sub(r'TZID=[^:]*:', '', dt_string)
            
            if 'T' in clean_dt:
                if clean_dt.endswith('Z'):
                    # UTC time
                    dt = datetime.strptime(clean_dt, '%Y%m%dT%H%M%SZ')
                    return dt.replace(tzinfo=pytz.UTC), False
                else:
                    # Local time or timezone specified
                    dt = datetime.strptime(clean_dt, '%Y%m%dT%H%M%S')
                    return dt.replace(tzinfo=pytz.UTC), False
            else:
                # Date only
                dt = datetime.strptime(clean_dt, '%Y%m%d')
                return dt.replace(tzinfo=pytz.UTC), True
                
        except ValueError:
            # Fallback: try dateutil parser
            try:
                dt = parse_date(dt_string)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=pytz.UTC)
                return dt, 'T' not in dt_string
            except:
                # Last resort: current time
                return datetime.now(pytz.UTC), False
    
    def _extract_uid_from_caldav_event(self, event) -> str:
        """Extract UID from CalDAV event."""
        try:
            ical_data = event.data
            uid = self._extract_ical_field(ical_data, 'UID')
            return uid or str(hash(str(event)))
        except:
            return str(hash(str(event)))
    
    def _create_ical_event(self, event_data: CalendarEvent) -> str:
        """Create iCal format string from event data."""
        # Generate a UID
        uid = event_data.id or f"calsync-claude-{hash(str(event_data))}"
        
        # Format datetime
        if event_data.all_day:
            dtstart = event_data.start.strftime('%Y%m%d')
            dtend = event_data.end.strftime('%Y%m%d')
            dtstart_line = f"DTSTART;VALUE=DATE:{dtstart}"
            dtend_line = f"DTEND;VALUE=DATE:{dtend}"
        else:
            dtstart = event_data.start.strftime('%Y%m%dT%H%M%SZ')
            dtend = event_data.end.strftime('%Y%m%dT%H%M%SZ')
            dtstart_line = f"DTSTART:{dtstart}"
            dtend_line = f"DTEND:{dtend}"
        
        # Escape iCal special characters
        def escape_text(text):
            if not text:
                return ""
            return text.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
        
        # Build iCal event
        ical_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//CalSync Claude//CalSync Claude 2.0//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            dtstart_line,
            dtend_line,
            f"SUMMARY:{escape_text(event_data.summary)}",
        ]
        
        if event_data.description:
            ical_lines.append(f"DESCRIPTION:{escape_text(event_data.description)}")
        
        if event_data.location:
            ical_lines.append(f"LOCATION:{escape_text(event_data.location)}")
        
        # Add timestamps
        now = datetime.now(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
        ical_lines.extend([
            f"DTSTAMP:{now}",
            f"CREATED:{event_data.created.strftime('%Y%m%dT%H%M%SZ')}",
            f"LAST-MODIFIED:{now}",
            "END:VEVENT",
            "END:VCALENDAR"
        ])
        
        return '\r\n'.join(ical_lines)