"""iCloud Calendar service implementation with async support."""

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, AsyncIterator, Set
from urllib.parse import urljoin

import caldav
from caldav import DAVClient
import pytz
from dateutil.parser import parse as parse_date
from icalendar import Calendar, Event as ICalEvent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseCalendarService, CalendarServiceError, AuthenticationError, EventNotFoundError
from ..models import CalendarEvent, CalendarInfo, EventSource, ChangeSet
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
            
            # Get events from CalDAV with proper sync support
            try:
                if sync_token:
                    # Use CalDAV sync-collection for true incremental sync
                    # This returns only changed events; deletions will be exposed via get_change_set
                    events = await self._get_events_with_sync_token(calendar, sync_token)
                else:
                    # Fallback to date search for initial sync
                    # WARNING: This cannot detect deletions reliably
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
                        if updated_min:
                            event_updated = self._ensure_timezone_aware(formatted_event.updated)
                            min_updated = self._ensure_timezone_aware(updated_min)
                            if event_updated < min_updated:
                                continue
                        
                        yield formatted_event
                        events_yielded += 1
                        
                except Exception as e:
                    self.logger.warning(f"Failed to parse iCloud event: {e}")
                    continue
                    
        except Exception as e:
            raise CalendarServiceError(f"Failed to get iCloud events: {e}")

    async def get_change_set(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
        sync_token: Optional[str] = None,
    ) -> ChangeSet[CalendarEvent]:
        """Return changed events and explicit deletions using CalDAV sync-collection when possible."""
        self._ensure_authenticated()

        # Defaults for initial backfill
        if time_min is None:
            time_min = datetime.now(pytz.UTC) - timedelta(days=self.settings.sync_config.sync_past_days)
        if time_max is None:
            time_max = datetime.now(pytz.UTC) + timedelta(days=self.settings.sync_config.sync_future_days)

        try:
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")

            changed: Dict[str, CalendarEvent] = {}
            deleted_native_ids: set[str] = set()
            next_token: Optional[str] = None
            used_sync = bool(sync_token)

            if sync_token:
                # Use sync-collection REPORT to get deltas
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: calendar.calendar_home_set.client.request(
                        calendar.url,
                        "REPORT",
                        f"""<?xml version=\"1.0\" encoding=\"utf-8\" ?>
<D:sync-collection xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:sync-token>{sync_token}</D:sync-token>
  <D:sync-level>1</D:sync-level>
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
</D:sync-collection>""",
                        headers={"Content-Type": "application/xml; charset=utf-8"}
                    )
                )

                # Parse for changes and deletions
                events, deleted_hrefs, extracted_next = await self._parse_sync_collection_for_changes(response, calendar)
                next_token = extracted_next

                # Turn events into CalendarEvent and key by href
                for ev in events:
                    parsed = self._parse_caldav_event(ev)
                    if parsed:
                        native_id = str(ev.url)
                        changed[native_id] = parsed
                for href in deleted_hrefs:
                    deleted_native_ids.add(href)
            else:
                # Fallback: time range snapshot
                events = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: calendar.date_search(start=time_min, end=time_max)
                )
                count = 0
                for ev in events:
                    if max_results and count >= max_results:
                        break
                    parsed = self._parse_caldav_event(ev)
                    if parsed:
                        if updated_min:
                            parsed_updated = self._ensure_timezone_aware(parsed.updated)
                            min_updated = self._ensure_timezone_aware(updated_min)
                            if parsed_updated < min_updated:
                                continue
                        native_id = str(ev.url) if hasattr(ev, 'url') else parsed.id
                        changed[native_id] = parsed
                        count += 1
                # Try to get a token for next run
                try:
                    props = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: calendar.get_properties([caldav.dav.GetCTag()])
                    )
                    next_token = props.get(caldav.dav.GetCTag.tag)
                except Exception:
                    next_token = None

            return ChangeSet[CalendarEvent](
                changed=changed,
                deleted_native_ids=deleted_native_ids,
                next_sync_token=next_token,
                used_sync_token=used_sync,
            )
        except Exception as e:
            if "401" in str(e) or "unauthor" in str(e).lower():
                raise AuthenticationError("iCloud authentication failed. Ensure an app-specific password is set.")
            if "429" in str(e) or "throttl" in str(e).lower():
                raise CalendarServiceError(f"iCloud throttled: {e}")
            raise CalendarServiceError(f"Failed to get iCloud change set: {e}")
    
    async def get_changes(
        self,
        calendar_id: str,
        *,
        updated_min: Optional[datetime] = None,
        sync_token: Optional[str] = None,
    ) -> ChangeSet[CalendarEvent]:
        """Get changes from iCloud Calendar - delegates to get_change_set."""
        return await self.get_change_set(
            calendar_id=calendar_id,
            time_min=updated_min,
            sync_token=sync_token,
            updated_min=updated_min
        )

    
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

    async def delete_resource_by_href(self, calendar_id: str, href: str) -> None:
        """Delete a CalDAV resource directly by its href."""
        self._ensure_authenticated()
        try:
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.calendar_home_set.client.request(href, "DELETE")
            )
        except Exception as e:
            raise CalendarServiceError(f"Failed to delete iCloud resource {href}: {e}")

    async def add_exdate_to_resource(self, calendar_id: str, href: str, recurrence_id_iso: str, all_day: bool = False) -> None:
        """Fetch the ICS at href, add EXDATE for the given recurrence, and save back."""
        self._ensure_authenticated()
        try:
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")
            # Find the event by href
            events = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )
            target = None
            for ev in events:
                if str(ev.url) == href:
                    target = ev
                    break
            if not target:
                raise EventNotFoundError(f"Resource {href} not found")
            # Parse and add EXDATE
            cal = Calendar.from_ical(target.data)
            vevent = next((c for c in cal.walk() if c.name == 'VEVENT'), None)
            if not vevent:
                raise CalendarServiceError("Invalid ICS: missing VEVENT")
            from dateutil.parser import isoparse
            rid = isoparse(recurrence_id_iso)
            if all_day:
                rid = rid.date()
            vevent.add('exdate', rid)
            # Increment SEQUENCE if present
            try:
                seq = int(vevent.get('sequence', 0)) + 1
                vevent['sequence'] = seq
            except Exception:
                pass
            updated_ics = cal.to_ical().decode('utf-8')
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: setattr(target, 'data', updated_ics) or target.save()
            )
        except Exception as e:
            raise CalendarServiceError(f"Failed to add EXDATE to {href}: {e}")
    
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
            
            # Handle timezone extraction and validation
            timezone = None
            if not all_day and hasattr(start_dt, 'tzinfo') and start_dt.tzinfo:
                timezone = self._validate_and_extract_timezone(start_dt.tzinfo)
            
            # Convert to datetime and handle all-day events
            if all_day:
                # Keep date format for all-day events
                start_dt = datetime.combine(start_dt, datetime.min.time())
                if dtend:
                    end_dt = datetime.combine(dtend.dt, datetime.min.time())
                else:
                    end_dt = start_dt + timedelta(days=1)
            else:
                # Ensure timezone-aware datetimes with proper validation
                start_dt = self._ensure_timezone_aware(start_dt)
                if dtend:
                    end_dt = self._ensure_timezone_aware(dtend.dt)
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
            
            # Extract recurrence overrides (RDATE, EXDATE, RECURRENCE-ID)
            recurrence_overrides = []
            for prop in ['rdate', 'exdate']:
                if prop in vevent:
                    recurrence_overrides.append({
                        'type': prop,
                        'dates': [str(d) for d in vevent[prop].to_ical().decode().split(',')]
                    })
            
            # CRITICAL: Handle RECURRENCE-ID for event overrides
            recurrence_id = vevent.get('recurrence-id')
            if recurrence_id:
                # This is a recurrence override event
                recurrence_overrides.append({
                    'type': 'recurrence-id',
                    'recurrence_id': str(recurrence_id.dt),
                    'is_override': True,
                    'original_uid': uid  # Same UID as master event
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
            elif override['type'] == 'recurrence-id' and override.get('is_override'):
                # CRITICAL: Add RECURRENCE-ID for recurrence exception events
                try:
                    recurrence_id_dt = parse_date(override['recurrence_id'])
                    event.add('recurrence-id', recurrence_id_dt)
                except:
                    self.logger.warning(f"Failed to parse RECURRENCE-ID: {override.get('recurrence_id')}")
                    continue
        
        cal.add_component(event)
        return cal.to_ical().decode('utf-8')
    
    async def _get_events_with_sync_token(self, calendar, sync_token: str):
        """Get events using CalDAV sync-collection for true incremental sync.
        
        This implements RFC 6578 - Collection Synchronization for WebDAV
        to get only changed/deleted events since the last sync.
        """
        try:
            # CalDAV sync-collection request
            # This will return only events that changed since the sync_token
            sync_query = f"""<?xml version="1.0" encoding="utf-8" ?>
<D:sync-collection xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:sync-token>{sync_token}</D:sync-token>
    <D:sync-level>1</D:sync-level>
    <D:prop>
        <D:getetag/>
        <C:calendar-data/>
    </D:prop>
</D:sync-collection>"""

            # Execute the sync query
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.calendar_home_set.client.request(
                    calendar.url, 
                    "REPORT", 
                    sync_query,
                    headers={"Content-Type": "application/xml; charset=utf-8"}
                )
            )
            
            # Parse the sync-collection response
            events = await self._parse_sync_collection_response(response, calendar)
            return events
            
        except Exception as e:
            self.logger.error(f"CalDAV sync-collection failed: {e}")
            # Fall back to regular date search
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )
    
    async def _parse_sync_collection_response(self, response, calendar):
        """Parse CalDAV sync-collection XML response to extract events.

        DEPRECATED: use _parse_sync_collection_for_changes for change sets.
        """
        import xml.etree.ElementTree as ET
        
        try:
            # Parse XML response
            root = ET.fromstring(response.content.decode('utf-8'))
            
            # Namespace mappings for CalDAV
            namespaces = {
                'D': 'DAV:',
                'C': 'urn:ietf:params:xml:ns:caldav'
            }
            
            events = []
            deleted_hrefs = []
            
            # Find all response elements
            for response_elem in root.findall('.//D:response', namespaces):
                href_elem = response_elem.find('D:href', namespaces)
                if href_elem is None:
                    continue
                    
                href = href_elem.text
                
                # Check if this is a deletion (status 404)
                status_elem = response_elem.find('.//D:status', namespaces)
                if status_elem is not None and '404' in status_elem.text:
                    deleted_hrefs.append(href)
                    continue
                
                # Check for calendar data
                calendar_data_elem = response_elem.find('.//C:calendar-data', namespaces)
                if calendar_data_elem is not None and calendar_data_elem.text:
                    try:
                        # Create a mock CalDAV event object
                        class MockCalDAVEvent:
                            def __init__(self, data, url):
                                self.data = data
                                self.url = url
                        
                        mock_event = MockCalDAVEvent(calendar_data_elem.text, href)
                        events.append(mock_event)
                        
                    except Exception as e:
                        self.logger.warning(f"Failed to parse calendar data for {href}: {e}")
                        continue
            
            # Log sync results
            if deleted_hrefs:
                self.logger.info(f"CalDAV sync found {len(deleted_hrefs)} deleted events")
            self.logger.info(f"CalDAV sync found {len(events)} changed events")
            
            return events
            
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse CalDAV sync-collection XML response: {e}")
            # Fall back to regular events query
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )

    async def _parse_sync_collection_for_changes(self, response, calendar):
        """Parse CalDAV sync-collection XML to return (events, deleted_hrefs, next_sync_token)."""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(response.content.decode('utf-8'))
            namespaces = {
                'D': 'DAV:',
                'C': 'urn:ietf:params:xml:ns:caldav'
            }
            events = []
            deleted_hrefs: List[str] = []
            next_token = None

            # Next token may appear in D:sync-token
            token_elem = root.find('.//D:sync-token', namespaces)
            if token_elem is not None and token_elem.text:
                next_token = token_elem.text

            for response_elem in root.findall('.//D:response', namespaces):
                href_elem = response_elem.find('D:href', namespaces)
                if href_elem is None:
                    continue
                href = href_elem.text

                status_elem = response_elem.find('.//D:status', namespaces)
                if status_elem is not None and '404' in status_elem.text:
                    deleted_hrefs.append(href)
                    continue

                calendar_data_elem = response_elem.find('.//C:calendar-data', namespaces)
                if calendar_data_elem is not None and calendar_data_elem.text:
                    class MockCalDAVEvent:
                        def __init__(self, data, url):
                            self.data = data
                            self.url = url
                    events.append(MockCalDAVEvent(calendar_data_elem.text, href))

            return events, deleted_hrefs, next_token
        except Exception as e:
            self.logger.error(f"Failed to parse sync-collection for changes: {e}")
            return await self._parse_sync_collection_response(response, calendar), [], None
        except Exception as e:
            self.logger.error(f"Error parsing sync-collection response: {e}")
            # Fall back to regular events query
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.events()
            )
    
    async def get_sync_token(self, calendar_id: str) -> str:
        """Get a sync token for incremental CalDAV sync.
        
        Args:
            calendar_id: iCloud calendar ID (CalDAV URL)
            
        Returns:
            Sync token (CTag) for future incremental calls
        """
        self._ensure_authenticated()
        
        try:
            self.logger.info(f"🔍 iCloud CalDAV: Looking up calendar by ID: {calendar_id}")
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                self.logger.error(f"❌ iCloud CalDAV: Calendar not found: {calendar_id}")
                raise CalendarServiceError(f"iCloud calendar {calendar_id} not found")
            
            self.logger.info(f"✅ iCloud CalDAV: Calendar found - URL: {calendar.url}")
            
            # Get the current CTag (Calendar Collection Tag)
            # This serves as our sync token for CalDAV
            self.logger.info(f"📊 iCloud CalDAV: Requesting calendar properties (CTag)")
            
            props = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.get_properties([caldav.dav.GetCTag()])
            )
            
            self.logger.info(f"📥 iCloud CalDAV: Properties response received")
            self.logger.info(f"🔍 iCloud CalDAV: Available properties: {list(props.keys()) if props else 'None'}")
            
            ctag = props.get(caldav.dav.GetCTag.tag) if props else None
            self.logger.info(f"🏷️  iCloud CalDAV: CTag extraction - CTag tag: {caldav.dav.GetCTag.tag}")
            self.logger.info(f"🏷️  iCloud CalDAV: CTag value: {repr(ctag)}")
            
            if not ctag:
                self.logger.error(f"❌ iCloud CalDAV: No CTag found in properties response")
                self.logger.error(f"🔍 iCloud CalDAV: Full properties dump: {props}")
                raise CalendarServiceError("No CTag returned from iCloud CalDAV")
                
            self.logger.info(f"🎯 iCloud CalDAV: CTag acquired successfully: {ctag}")
            return ctag
            
        except Exception as e:
            raise CalendarServiceError(f"Failed to get iCloud sync token: {e}")
    
    def _extract_ical_field(self, ical_data: str, field_name: str) -> Optional[str]:
        """Extract a field value from iCal data using regex.
        
        Args:
            ical_data: Raw iCal data string
            field_name: Field name to extract (e.g., 'UID', 'SUMMARY')
            
        Returns:
            Field value or None if not found
        """
        try:
            pattern = rf'^{field_name}:(.*)$'
            match = re.search(pattern, ical_data, re.MULTILINE)
            if match:
                return match.group(1).strip()
            return None
        except Exception as e:
            self.logger.warning(f"Error extracting {field_name} from iCal data: {e}")
            return None
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test iCloud CalDAV connection.
        
        Returns:
            Dictionary with connection test results
        """
        try:
            if not self._authenticated:
                await self.authenticate()
            
            # Try to get calendars as connection test
            calendars = await self.get_calendars()
            
            return {
                'success': True,
                'calendars_found': len(calendars),
                'server_url': self.settings.icloud_server_url,
                'username': self.settings.icloud_username
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'error_type': type(e).__name__,
                'server_url': self.settings.icloud_server_url,
                'username': self.settings.icloud_username
            }
    
    def _ensure_authenticated(self) -> None:
        """Ensure service is authenticated, raise error if not."""
        if not self._authenticated:
            raise CalendarServiceError("iCloud service not authenticated. Call authenticate() first.")
    
    async def get_calendar_info(self, calendar_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed calendar information.
        
        Args:
            calendar_id: iCloud calendar ID (CalDAV URL)
            
        Returns:
            Dictionary with calendar details or None if not found
        """
        self._ensure_authenticated()
        
        try:
            calendar = await self._find_calendar_by_id(calendar_id)
            if not calendar:
                return None
            
            # Get calendar properties
            props = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: calendar.get_properties([
                    caldav.dav.DisplayName(),
                    caldav.dav.GetCTag(),
                    caldav.dav.SupportedCalendarComponentSet()
                ])
            )
            
            return {
                'id': calendar_id,
                'url': str(calendar.url),
                'name': props.get(caldav.dav.DisplayName.tag, 'Unknown'),
                'ctag': props.get(caldav.dav.GetCTag.tag),
                'supported_components': props.get(caldav.dav.SupportedCalendarComponentSet.tag, [])
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get iCloud calendar info: {e}")
            return None
    
    def _validate_and_extract_timezone(self, tzinfo) -> Optional[str]:
        """Validate and extract timezone string from tzinfo object.
        
        Args:
            tzinfo: Timezone info object
            
        Returns:
            Valid IANA timezone string or None
        """
        try:
            timezone_str = str(tzinfo)
            
            # Handle common timezone formats
            if hasattr(tzinfo, 'zone'):
                # pytz timezone
                timezone_str = tzinfo.zone
            elif timezone_str.startswith('UTC'):
                timezone_str = 'UTC'
            elif timezone_str in ['CET', 'EST', 'PST', 'MST']:
                # Common abbreviations - convert to IANA
                timezone_map = {
                    'CET': 'Europe/Berlin',
                    'EST': 'America/New_York',
                    'PST': 'America/Los_Angeles',
                    'MST': 'America/Denver'
                }
                timezone_str = timezone_map.get(timezone_str, 'UTC')
            
            # Validate it's a known timezone
            try:
                pytz.timezone(timezone_str)
                return timezone_str
            except pytz.exceptions.UnknownTimeZoneError:
                self.logger.warning(f"Unknown timezone: {timezone_str}, defaulting to UTC")
                return 'UTC'
                
        except Exception as e:
            self.logger.warning(f"Error extracting timezone: {e}, defaulting to UTC")
            return 'UTC'
    
    def _ensure_timezone_aware(self, dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware.
        
        Args:
            dt: Datetime object
            
        Returns:
            Timezone-aware datetime
        """
        if dt.tzinfo is None:
            # Naive datetime - assume UTC
            return dt.replace(tzinfo=pytz.UTC)
        elif dt.tzinfo.utcoffset(dt) is None:
            # Invalid timezone info
            return dt.replace(tzinfo=pytz.UTC)
        else:
            # Already timezone-aware
            return dt