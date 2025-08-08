"""Google Calendar service implementation with async support."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, AsyncIterator
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httpx
import pytz
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseCalendarService, CalendarServiceError, AuthenticationError, EventNotFoundError
from ..models import CalendarEvent, CalendarInfo, EventSource
from ..config import Settings


class GoogleCalendarService(BaseCalendarService):
    """Google Calendar service with async support."""
    
    def __init__(self, settings: Settings):
        """Initialize Google Calendar service.
        
        Args:
            settings: Application settings
        """
        super().__init__(settings, EventSource.GOOGLE)
        self.service = None
        self._http_client = None
    
    async def authenticate(self) -> None:
        """Authenticate with Google Calendar API."""
        try:
            creds = None
            token_path = self.settings.google_token_path
            
            # Load existing token if it exists
            if token_path.exists():
                creds = Credentials.from_authorized_user_file(
                    str(token_path), 
                    self.settings.google_scopes
                )
            
            # If there are no valid credentials available, authenticate
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    # Refresh expired credentials
                    creds.refresh(Request())
                else:
                    # Create credentials file for OAuth flow
                    await self._create_credentials_file()
                    
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.settings.google_credentials_path),
                        self.settings.google_scopes
                    )
                    creds = flow.run_local_server(port=0)
                
                # Save credentials for next run
                token_path.parent.mkdir(parents=True, exist_ok=True)
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())
            
            # Build the service
            self.service = build('calendar', 'v3', credentials=creds)
            
            # Initialize HTTP client for async requests
            self._http_client = httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                limits=httpx.Limits(
                    max_connections=self.settings.max_concurrent_requests
                )
            )
            
            self._authenticated = True
            self.logger.info("Successfully authenticated with Google Calendar")
            
        except Exception as e:
            raise AuthenticationError(f"Google Calendar authentication failed: {e}")
    
    async def _create_credentials_file(self) -> None:
        """Create Google OAuth credentials file."""
        credentials_data = {
            "installed": {
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
            }
        }
        
        credentials_path = self.settings.google_credentials_path
        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(credentials_path, 'w') as f:
            json.dump(credentials_data, f)
    
    async def get_calendars(self) -> List[CalendarInfo]:
        """Get list of Google calendars."""
        self._ensure_authenticated()
        
        try:
            # Run synchronous API call in thread pool
            calendar_list = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.calendarList().list().execute()
            )
            
            calendars = []
            for cal_data in calendar_list.get('items', []):
                calendar_info = CalendarInfo(
                    id=cal_data['id'],
                    name=cal_data.get('summary', 'Unnamed Calendar'),
                    source=EventSource.GOOGLE,
                    description=cal_data.get('description'),
                    timezone=cal_data.get('timeZone', 'UTC'),
                    color=cal_data.get('backgroundColor'),
                    access_role=cal_data.get('accessRole'),
                    is_primary=cal_data.get('primary', False),
                    is_selected=cal_data['id'] in self.settings.sync_config.selected_google_calendars
                    if self.settings.sync_config.selected_google_calendars
                    else cal_data.get('primary', False)
                )
                calendars.append(calendar_info)
            
            return calendars
            
        except Exception as e:
            raise CalendarServiceError(f"Failed to get Google calendars: {e}")
    
    async def get_primary_calendar(self) -> CalendarInfo:
        """Get primary Google calendar."""
        calendars = await self.get_calendars()
        
        # Find primary calendar
        for calendar in calendars:
            if calendar.is_primary:
                return calendar
        
        # Fallback to first calendar
        if calendars:
            return calendars[0]
        
        raise CalendarServiceError("No Google calendars found")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((HttpError, CalendarServiceError))
    )
    async def get_events(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
    ) -> AsyncIterator[CalendarEvent]:
        """Get events from Google calendar asynchronously."""
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
            page_token = None
            events_yielded = 0
            
            while True:
                # Build request parameters
                params = {
                    'calendarId': calendar_id,
                    'timeMin': time_min.isoformat(),
                    'timeMax': time_max.isoformat(),
                    'singleEvents': True,
                    'orderBy': 'startTime',
                    'maxResults': min(250, max_results or 250)
                }
                
                if updated_min:
                    params['updatedMin'] = updated_min.isoformat()
                if page_token:
                    params['pageToken'] = page_token
                
                # Execute API call with rate limit handling
                try:
                    events_result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.service.events().list(**params).execute()
                    )
                except HttpError as e:
                    if e.resp.status == 429:  # Rate limited
                        self.logger.warning("Google API rate limited, retrying...")
                        raise CalendarServiceError(f"Rate limited: {e}")
                    raise
                
                # Process events
                for event_data in events_result.get('items', []):
                    if max_results and events_yielded >= max_results:
                        return
                    
                    try:
                        event = self._format_google_event(event_data)
                        yield event
                        events_yielded += 1
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to format Google event {event_data.get('id')}: {e}"
                        )
                        continue
                
                # Check for next page
                page_token = events_result.get('nextPageToken')
                if not page_token:
                    break
                
        except HttpError as e:
            if e.resp.status == 404:
                raise CalendarServiceError(f"Google calendar {calendar_id} not found")
            raise CalendarServiceError(f"Failed to get Google events: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to get Google events: {e}")
    
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        """Get a specific Google Calendar event."""
        self._ensure_authenticated()
        
        try:
            event_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().get(
                    calendarId=calendar_id,
                    eventId=event_id
                ).execute()
            )
            return self._format_google_event(event_data)
            
        except HttpError as e:
            if e.resp.status == 404:
                raise EventNotFoundError(f"Google event {event_id} not found")
            raise CalendarServiceError(f"Failed to get Google event: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to get Google event: {e}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((HttpError, CalendarServiceError))
    )
    async def create_event(
        self,
        calendar_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Create a new Google Calendar event."""
        self._ensure_authenticated()
        
        try:
            google_event_data = self._convert_to_google_format(event_data)
            
            created_event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().insert(
                    calendarId=calendar_id,
                    body=google_event_data
                ).execute()
            )
            
            return self._format_google_event(created_event)
            
        except Exception as e:
            raise CalendarServiceError(f"Failed to create Google event: {e}")
    
    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Update a Google Calendar event."""
        self._ensure_authenticated()
        
        try:
            google_event_data = self._convert_to_google_format(event_data)
            
            updated_event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().update(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body=google_event_data
                ).execute()
            )
            
            return self._format_google_event(updated_event)
            
        except HttpError as e:
            if e.resp.status == 404:
                raise EventNotFoundError(f"Google event {event_id} not found")
            raise CalendarServiceError(f"Failed to update Google event: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to update Google event: {e}")
    
    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete a Google Calendar event."""
        self._ensure_authenticated()
        
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().delete(
                    calendarId=calendar_id,
                    eventId=event_id
                ).execute()
            )
            
        except HttpError as e:
            if e.resp.status == 404:
                raise EventNotFoundError(f"Google event {event_id} not found")
            raise CalendarServiceError(f"Failed to delete Google event: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to delete Google event: {e}")
    
    def _format_google_event(self, event_data: Dict[str, Any]) -> CalendarEvent:
        """Convert Google Calendar event to standard format."""
        # Handle different date/time formats
        start = event_data.get('start', {})
        end = event_data.get('end', {})
        
        # Check if it's an all-day event
        all_day = 'date' in start
        
        timezone = None
        if all_day:
            # For all-day events, keep date format without timezone conversion
            start_dt = datetime.fromisoformat(start['date'])
            end_dt = datetime.fromisoformat(end['date'])
        else:
            # Extract timezone from dateTime
            start_tz_str = start.get('timeZone')
            if start_tz_str:
                timezone = start_tz_str
            
            start_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end['dateTime'].replace('Z', '+00:00'))
        
        # Parse attendees
        attendees = []
        for attendee in event_data.get('attendees', []):
            attendees.append({
                'email': attendee.get('email', ''),
                'displayName': attendee.get('displayName', ''),
                'responseStatus': attendee.get('responseStatus', 'needsAction'),
                'organizer': attendee.get('organizer', False)
            })
        
        # Extract recurrence information
        recurrence_rule = None
        if 'recurrence' in event_data and event_data['recurrence']:
            recurrence_rule = event_data['recurrence'][0]  # First RRULE
        
        # Generate or use UID - Google events use iCalUID for deduplication
        uid = event_data.get('iCalUID', f"google-{event_data['id']}")
        
        return CalendarEvent(
            id=event_data['id'],
            uid=uid,
            source=EventSource.GOOGLE,
            summary=event_data.get('summary', ''),
            description=event_data.get('description', ''),
            location=event_data.get('location', ''),
            start=start_dt,
            end=end_dt,
            all_day=all_day,
            timezone=timezone,
            created=datetime.fromisoformat(event_data['created'].replace('Z', '+00:00')),
            updated=datetime.fromisoformat(event_data['updated'].replace('Z', '+00:00')),
            etag=event_data.get('etag'),
            sequence=event_data.get('sequence', 0),
            recurring_event_id=event_data.get('recurringEventId'),
            recurrence_rule=recurrence_rule,
            organizer=event_data.get('organizer'),
            attendees=attendees,
            original_data=event_data
        )
    
    def _convert_to_google_format(self, event: CalendarEvent) -> Dict[str, Any]:
        """Convert standard event format to Google Calendar format."""
        google_event = {
            'summary': event.summary,
            'description': event.description or '',
            'location': event.location or ''
        }
        
        if event.all_day:
            # All-day event
            google_event['start'] = {'date': event.start.strftime('%Y-%m-%d')}
            google_event['end'] = {'date': event.end.strftime('%Y-%m-%d')}
        else:
            # Timed event
            google_event['start'] = {'dateTime': event.start.isoformat()}
            google_event['end'] = {'dateTime': event.end.isoformat()}
        
        # Add attendees if present
        if event.attendees:
            google_event['attendees'] = []
            for attendee in event.attendees:
                google_attendee = {
                    'email': attendee.get('email', ''),
                    'responseStatus': attendee.get('responseStatus', 'needsAction')
                }
                if attendee.get('displayName'):
                    google_attendee['displayName'] = attendee['displayName']
                google_event['attendees'].append(google_attendee)
        
        return google_event
    
    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client:
            await self._http_client.aclose()