"""Google Calendar service implementation with async support."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, AsyncIterator, Tuple, Set
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
from ..models import CalendarEvent, CalendarInfo, EventSource, ChangeSet
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
                    
                    # Check if running in Docker (headless) or local environment
                    import os
                    is_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == 'true'
                    
                    if is_docker:
                        # Headless Docker environment - provide setup instructions
                        self.logger.error(
                            f"\n{'='*80}\n"
                            f"GOOGLE OAUTH SETUP REQUIRED FOR HEADLESS DEPLOYMENT\n"
                            f"{'='*80}\n"
                            f"Option 1 - Generate token locally and copy:\n"
                            f"1. On your local machine with a browser, run:\n"
                            f"   pip install calsync-claude\n"
                            f"   calsync-claude test\n"
                            f"2. Complete OAuth in browser\n"
                            f"3. Copy the token file:\n"
                            f"   scp ~/.calsync-claude/credentials/google_token.json \\\n"
                            f"       root@your-vps:./data/credentials/\n\n"
                            f"Current credentials path: {self.settings.google_token_path}\n"
                            f"{'='*80}"
                        )
                        raise AuthenticationError(
                            f"Google OAuth token not found. Please follow setup instructions in logs above."
                        )
                    else:
                        # Local environment with browser - use normal OAuth flow
                        self.logger.info("Starting Google OAuth flow in browser...")
                        creds = flow.run_local_server(port=0)
                        self.logger.info("OAuth flow completed successfully")
                
                # Save credentials for next run with secure permissions
                token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())
                # Set secure file permissions (owner read/write only)
                token_path.chmod(0o600)
            
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
        # Check if running in Docker (headless) or local environment
        import os
        is_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == 'true'
        
        if is_docker:
            # For headless/server deployment, use OOB (though deprecated)
            redirect_uris = ["urn:ietf:wg:oauth:2.0:oob"]
        else:
            # For local development, use localhost redirect URIs
            redirect_uris = ["http://localhost"]
        
        credentials_data = {
            "installed": {
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": redirect_uris
            }
        }
        
        credentials_path = self.settings.google_credentials_path
        credentials_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        
        with open(credentials_path, 'w') as f:
            json.dump(credentials_data, f)
        # Set secure file permissions (owner read/write only)
        credentials_path.chmod(0o600)
    
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
        sync_token: Optional[str] = None,
    ) -> AsyncIterator[CalendarEvent]:
        """Get events from Google calendar asynchronously with sync token support."""
        self._ensure_authenticated()
        
        try:
            page_token = None
            events_yielded = 0
            next_sync_token = None
            
            while True:
                # Build request parameters
                params = {
                    'calendarId': calendar_id,
                    'maxResults': min(250, max_results or 250)
                }
                
                # CRITICAL: Use sync token for true incremental sync when available
                if sync_token:
                    # Sync token mode - gets ALL changes since last sync (including deletes)
                    params['syncToken'] = sync_token
                    # IMPORTANT: Do NOT use time filters with sync tokens
                    # Sync tokens return all events that changed, regardless of time
                else:
                    # Time window mode - ONLY for initial sync
                    # WARNING: This mode cannot detect deletions reliably
                    if time_min is None:
                        time_min = datetime.now(pytz.UTC) - timedelta(
                            days=self.settings.sync_config.sync_past_days
                        )
                    if time_max is None:
                        time_max = datetime.now(pytz.UTC) + timedelta(
                            days=self.settings.sync_config.sync_future_days
                        )
                    
                    params.update({
                        'timeMin': time_min.isoformat(),
                        'timeMax': time_max.isoformat(),
                        'singleEvents': True,
                        'orderBy': 'startTime'
                    })
                    
                    # NOTE: updatedMin is redundant with sync tokens but useful for time windows
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
                    # Skip cancelled events here; deletions are handled in get_change_set
                    if event_data.get('status') == 'cancelled':
                        continue
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
                
                # Check for next page or sync token
                page_token = events_result.get('nextPageToken')
                next_sync_token = events_result.get('nextSyncToken')
                
                if not page_token:
                    # Store the sync token for future incremental syncs
                    if next_sync_token and hasattr(self, '_current_sync_token_callback'):
                        self._current_sync_token_callback(next_sync_token)
                    break
                
        except HttpError as e:
            if e.resp.status == 404:
                raise CalendarServiceError(f"Google calendar {calendar_id} not found")
            raise CalendarServiceError(f"Failed to get Google events: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to get Google events: {e}")

    class TokenInvalid(Exception):
        pass

    async def get_change_set(
        self,
        calendar_id: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None,
        updated_min: Optional[datetime] = None,
        sync_token: Optional[str] = None,
    ) -> ChangeSet[CalendarEvent]:
        """Return changed events and explicit deletions.
        - If sync_token is provided, use true incremental with showDeleted.
        - If token invalid (410), raise TokenInvalid.
        - If no token, use time window and return snapshot (no deletions).
        """
        self._ensure_authenticated()
        changed: Dict[str, CalendarEvent] = {}
        deleted_ids: set[str] = set()
        next_sync_token: Optional[str] = None
        page_token: Optional[str] = None

        def _build_params() -> Dict[str, Any]:
            params: Dict[str, Any] = {
                'calendarId': calendar_id,
                'maxResults': min(250, max_results or 250)
            }
            if sync_token:
                params['syncToken'] = sync_token
            else:
                nonlocal time_min, time_max
                if time_min is None:
                    time_min = datetime.now(pytz.UTC) - timedelta(days=self.settings.sync_config.sync_past_days)
                if time_max is None:
                    time_max = datetime.now(pytz.UTC) + timedelta(days=self.settings.sync_config.sync_future_days)
                params.update({
                    'timeMin': time_min.isoformat(),
                    'timeMax': time_max.isoformat(),
                    'singleEvents': True,
                    'orderBy': 'startTime'
                })
                if updated_min:
                    params['updatedMin'] = updated_min.isoformat()
            if page_token:
                params['pageToken'] = page_token
            return params

        used_sync = bool(sync_token)
        try:
            while True:
                params = _build_params()
                if sync_token:
                    params['showDeleted'] = True
                    params['singleEvents'] = True
                    params['maxResults'] = min(2500, max_results or 2500)
                try:
                    events_result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.service.events().list(**params).execute()
                    )
                except HttpError as e:
                    if e.resp.status == 429:
                        self.logger.warning("Google API rate limited, retrying...")
                        raise CalendarServiceError(f"Rate limited: {e}")
                    if e.resp.status == 410 and sync_token:
                        self.logger.warning("Google sync token expired/invalid (410)")
                        raise GoogleCalendarService.TokenInvalid()
                    raise

                for event_data in events_result.get('items', []):
                    status = event_data.get('status')
                    event_id = event_data.get('id')
                    if status == 'cancelled':
                        if event_id:
                            deleted_ids.add(event_id)
                        continue
                    try:
                        ev = self._format_google_event(event_data)
                    except Exception as e:
                        self.logger.warning(f"Failed to format Google event {event_id}: {e}")
                        continue
                    changed[ev.id] = ev

                page_token = events_result.get('nextPageToken')
                next_sync_token = events_result.get('nextSyncToken') or next_sync_token
                if not page_token:
                    break

            if next_sync_token and hasattr(self, '_current_sync_token_callback'):
                self._current_sync_token_callback(next_sync_token)

            return ChangeSet[CalendarEvent](
                changed=changed,
                deleted_native_ids=deleted_ids,
                next_sync_token=next_sync_token,
                used_sync_token=used_sync,
            )
        except HttpError as e:
            if e.resp.status == 404:
                raise CalendarServiceError(f"Google calendar {calendar_id} not found")
            raise CalendarServiceError(f"Failed to get Google change set: {e}")
        except GoogleCalendarService.TokenInvalid:
            raise
        except Exception as e:
            raise CalendarServiceError(f"Failed to get Google change set: {e}")
    
    async def get_changes(
        self,
        calendar_id: str,
        *,
        updated_min: Optional[datetime] = None,
        sync_token: Optional[str] = None,
    ) -> ChangeSet[CalendarEvent]:
        """Get changes from Google Calendar - delegates to get_change_set."""
        return await self.get_change_set(
            calendar_id=calendar_id,
            time_min=updated_min,
            sync_token=sync_token,
            updated_min=updated_min
        )
    
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
    
    async def create_event(
        self,
        calendar_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Create a new Google Calendar event."""
        print(f"🚨 CREATE EVENT CALLED WITH ID: {calendar_id}")
        self.logger.critical(f"🚨 CREATE EVENT CALLED WITH ID: {calendar_id}")
        self._ensure_authenticated()
        
        self.logger.info(f"🔍 Creating Google Calendar event with ID: {calendar_id}")
        
        # CRITICAL FIX: Validate calendar ID BEFORE retry loop
        validated_calendar_id = await self._validate_calendar_id(calendar_id)
        self.logger.info(f"✅ Validated calendar ID: {validated_calendar_id}")
        
        # Now do the actual creation with retry using the validated ID
        return await self._create_event_with_retry(validated_calendar_id, event_data)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((HttpError, CalendarServiceError))
    )
    async def _create_event_with_retry(
        self,
        validated_calendar_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Create event with retry logic using validated calendar ID."""
        try:
            # CRITICAL: Use custom event ID to prevent duplicates during initial sync
            google_event_data = self._convert_to_google_format(event_data, use_event_id=True)
            
            created_event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().insert(
                    calendarId=validated_calendar_id,
                    body=google_event_data
                ).execute()
            )
            
            return self._format_google_event(created_event)
            
        except Exception as e:
            self.logger.error(f"❌ Create event failed with validated_calendar_id={validated_calendar_id}, error: {e}")
            raise CalendarServiceError(f"Failed to create Google event: {e}")
    
    async def _validate_calendar_id(self, calendar_id: str) -> str:
        """Validate and potentially fix calendar ID format issues.
        
        Args:
            calendar_id: The calendar ID to validate
            
        Returns:
            Validated calendar ID
            
        Raises:
            CalendarServiceError: If calendar ID is invalid
        """
        print(f"🚨 VALIDATION METHOD CALLED WITH ID: {calendar_id}")
        self.logger.critical(f"🚨 VALIDATION METHOD CALLED WITH ID: {calendar_id}")
        self.logger.info(f"🔍 Validating Google Calendar ID: {calendar_id}")
        
        try:
            # Check if calendar exists by trying to get its info
            calendar_info = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.calendars().get(calendarId=calendar_id).execute()
            )
            
            self.logger.info(f"✅ Calendar ID is valid: {calendar_id}")
            return calendar_id  # Calendar exists, ID is valid
            
        except HttpError as e:
            if e.resp.status == 404:
                # Calendar not found - try common fixes
                self.logger.warning(f"📋 Google Calendar ID not found: {calendar_id}")
                
                # Try to find the correct calendar ID by listing all calendars
                try:
                    calendar_list = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.service.calendarList().list().execute()
                    )
                    
                    # Look for primary calendar
                    for calendar_item in calendar_list.get('items', []):
                        if calendar_item.get('primary', False):
                            primary_id = calendar_item['id']
                            self.logger.warning(f"🔧 Using primary calendar instead: {primary_id}")
                            return primary_id
                    
                    # Fallback to 'primary'
                    self.logger.warning(f"🔧 Using 'primary' as fallback")
                    return 'primary'
                    
                except Exception as list_error:
                    self.logger.error(f"Failed to list Google calendars: {list_error}")
                    # Final fallback
                    return 'primary'
            else:
                # Other HTTP error, re-raise
                raise CalendarServiceError(f"Calendar ID validation failed: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error validating calendar ID {calendar_id}: {e}")
            # Fallback to primary
            return 'primary'
    
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

    async def find_instance_id(
        self,
        calendar_id: str,
        recurring_event_id: str,
        recurrence_id_iso: str
    ) -> Optional[str]:
        """Find a Google instance eventId by querying instances around the recurrence_id timestamp."""
        self._ensure_authenticated()
        try:
            # Use a tight window around the recurrence_id
            from dateutil.parser import isoparse
            rid = isoparse(recurrence_id_iso)
            time_min = (rid - timedelta(minutes=5)).isoformat()
            time_max = (rid + timedelta(minutes=5)).isoformat()
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().instances(
                    calendarId=calendar_id,
                    eventId=recurring_event_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=50
                ).execute()
            )
            for item in result.get('items', []):
                # Match on originalStartTime if present
                ost = item.get('originalStartTime', {}).get('dateTime') or item.get('originalStartTime', {}).get('date')
                if ost:
                    try:
                        if isoparse(ost) == rid:
                            return item.get('id')
                    except Exception:
                        continue
            return None
        except HttpError as e:
            if e.resp.status == 404:
                return None
            raise CalendarServiceError(f"Failed to query instances: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to query instances: {e}")
    
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
        recurrence_overrides = []
        if 'recurrence' in event_data and event_data['recurrence']:
            recurrence_rule = event_data['recurrence'][0]  # First RRULE
        
        # CRITICAL: Handle Google's RECURRENCE-ID overrides
        if 'recurringEventId' in event_data and event_data['recurringEventId']:
            # This is a recurrence override event in Google Calendar
            recurrence_overrides.append({
                'type': 'recurrence-id',
                'recurrence_id': start_dt.isoformat(),  # Use start time as recurrence ID
                'is_override': True,
                'master_event_id': event_data['recurringEventId'],
                'original_start': event_data.get('originalStartTime', {}).get('dateTime', start_dt.isoformat())
            })
        
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
            recurrence_overrides=recurrence_overrides,
            organizer=event_data.get('organizer'),
            attendees=attendees,
            original_data=event_data
        )
    
    def _convert_to_google_format(self, event: CalendarEvent, use_event_id: bool = False) -> Dict[str, Any]:
        """Convert standard event format to Google Calendar format."""
        google_event = {
            'summary': event.summary,
            'description': event.description or '',
            'location': event.location or ''
        }
        
        # CRITICAL: Set custom event ID when we already have a UID to prevent duplicates
        if use_event_id and event.uid:
            # Use a deterministic ID based on the UID to prevent duplicates
            import hashlib
            event_id = hashlib.sha1(event.uid.encode()).hexdigest()[:32]
            google_event['id'] = event_id
        
        # Set iCalUID for cross-platform matching
        if event.uid:
            google_event['iCalUID'] = event.uid
        
        if event.all_day:
            # All-day event
            google_event['start'] = {'date': event.start.strftime('%Y-%m-%d')}
            google_event['end'] = {'date': event.end.strftime('%Y-%m-%d')}
        else:
            # Timed event
            google_event['start'] = {'dateTime': event.start.isoformat()}
            google_event['end'] = {'dateTime': event.end.isoformat()}
        
        # Add sequence for conflict resolution
        if event.sequence is not None:
            google_event['sequence'] = event.sequence
        
        # Add recurrence rule if present
        if event.recurrence_rule:
            google_event['recurrence'] = [event.recurrence_rule]
        
        # CRITICAL: Handle recurrence overrides properly
        if event.recurrence_overrides:
            for override in event.recurrence_overrides:
                if override.get('type') == 'recurrence-id' and override.get('is_override'):
                    # This is a recurrence exception - set the recurringEventId
                    if override.get('master_event_id'):
                        google_event['recurringEventId'] = override['master_event_id']
                    
                    # Set original start time for Google Calendar
                    if override.get('original_start'):
                        google_event['originalStartTime'] = {
                            'dateTime': override['original_start']
                        }
        
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
    
    async def get_sync_token(self, calendar_id: str) -> str:
        """Get a sync token for incremental sync.
        
        Args:
            calendar_id: Google calendar ID
            
        Returns:
            Sync token for future incremental calls
        """
        self._ensure_authenticated()
        
        try:
            # FIXED: Google Calendar API sync token acquisition
            # The sync token is returned when using the events.list API WITHOUT time bounds
            # After getting the full list, the nextSyncToken allows incremental updates
            
            page_token = None
            sync_token = None
            
            self.logger.info(f"📊 Google API: Acquiring sync token without time bounds")
            page_count = 0
            total_events = 0
            
            while True:
                page_count += 1
                params = {
                    'calendarId': calendar_id,
                    'maxResults': 250,  # Max per page
                    'singleEvents': True,
                    'showDeleted': True,  # Required for sync tokens
                }
                if page_token:
                    params['pageToken'] = page_token
                
                self.logger.info(f"📄 Google API: Requesting page {page_count} (maxResults=250)")
                self.logger.info(f"🔧 Google API: Request params: {params}")
                
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.service.events().list(**params).execute()
                    )
                    self.logger.info(f"✅ Google API: Request successful")
                except Exception as e:
                    self.logger.error(f"❌ Google API: Request failed: {type(e).__name__}: {e}")
                    raise
                
                # Log response info
                result_keys = list(result.keys()) if result else []
                self.logger.info(f"🔍 Google API: Response keys: {result_keys}")
                
                events_this_page = len(result.get('items', []))
                total_events += events_this_page
                self.logger.info(f"📥 Google API: Page {page_count} returned {events_this_page} events (total: {total_events})")
                
                # Check for next page
                page_token = result.get('nextPageToken')
                sync_token_on_page = result.get('nextSyncToken')
                
                self.logger.info(f"🔄 Google API: Page {page_count} - nextPageToken: {'✅' if page_token else '❌'} | nextSyncToken: {'✅' if sync_token_on_page else '❌'}")
                
                # Sync token is only available on the final page
                if not page_token:
                    sync_token = sync_token_on_page
                    self.logger.info(f"🏁 Google API: Final page {page_count} reached - total events enumerated: {total_events}")
                    break
            
            if not sync_token:
                self.logger.error(f"❌ Google API: No nextSyncToken found after {page_count} pages ({total_events} events)")
                raise CalendarServiceError("No sync token returned from Google Calendar API after full pagination")
                
            self.logger.info(f"🎯 Google API: Sync token acquired successfully after {page_count} pages")
            return sync_token
            
        except HttpError as e:
            if e.resp.status == 404:
                raise CalendarServiceError(f"Google calendar {calendar_id} not found")
            raise CalendarServiceError(f"Failed to get sync token: {e}")
        except Exception as e:
            raise CalendarServiceError(f"Failed to get sync token: {e}")
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test Google Calendar API connection.
        
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
                'authenticated': self._authenticated,
                'credentials_path': str(self.settings.google_credentials_path),
                'token_path': str(self.settings.google_token_path)
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'error_type': type(e).__name__,
                'authenticated': self._authenticated,
                'credentials_path': str(self.settings.google_credentials_path),
                'token_path': str(self.settings.google_token_path)
            }
    
    def _ensure_authenticated(self) -> None:
        """Ensure service is authenticated, raise error if not."""
        if not self._authenticated:
            raise CalendarServiceError("Google service not authenticated. Call authenticate() first.")
    
    async def get_calendar_info(self, calendar_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed calendar information.
        
        Args:
            calendar_id: Google calendar ID
            
        Returns:
            Dictionary with calendar details or None if not found
        """
        self._ensure_authenticated()
        
        try:
            calendar_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.calendars().get(calendarId=calendar_id).execute()
            )
            
            return {
                'id': calendar_data['id'],
                'summary': calendar_data.get('summary', 'Unknown'),
                'description': calendar_data.get('description'),
                'location': calendar_data.get('location'),
                'timeZone': calendar_data.get('timeZone'),
                'etag': calendar_data.get('etag'),
                'kind': calendar_data.get('kind')
            }
            
        except HttpError as e:
            if e.resp.status == 404:
                return None
            self.logger.error(f"Failed to get Google calendar info: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get Google calendar info: {e}")
            return None
    
    def _format_datetime_for_google(self, dt: datetime, all_day: bool = False) -> Dict[str, str]:
        """Format datetime for Google Calendar API.
        
        Args:
            dt: Datetime object
            all_day: Whether this is an all-day event
            
        Returns:
            Dictionary with formatted datetime
        """
        if all_day:
            return {'date': dt.strftime('%Y-%m-%d')}
        else:
            return {'dateTime': dt.isoformat()}
    
    async def list_upcoming_events(
        self, 
        calendar_id: str, 
        max_results: int = 10,
        time_min: Optional[datetime] = None
    ) -> List[CalendarEvent]:
        """Get upcoming events from a Google calendar.
        
        Args:
            calendar_id: Google calendar ID
            max_results: Maximum number of events to return
            time_min: Minimum time for events (default: now)
            
        Returns:
            List of upcoming calendar events
        """
        if time_min is None:
            time_min = datetime.now(pytz.UTC)
        
        events = []
        async for event in self.get_events(
            calendar_id, 
            time_min=time_min,
            max_results=max_results
        ):
            events.append(event)
            
        return events
    
    async def batch_update_events(
        self,
        calendar_id: str,
        event_updates: List[Tuple[str, CalendarEvent]]
    ) -> List[Dict[str, Any]]:
        """Update multiple events in batch.
        
        Args:
            calendar_id: Google calendar ID
            event_updates: List of (event_id, event_data) tuples
            
        Returns:
            List of update results
        """
        results = []
        
        # Google Calendar API doesn't have native batch update for events
        # Process sequentially with rate limiting
        for event_id, event_data in event_updates:
            try:
                updated_event = await self.update_event(calendar_id, event_id, event_data)
                results.append({
                    'event_id': event_id,
                    'success': True,
                    'updated_event': updated_event
                })
            except Exception as e:
                results.append({
                    'event_id': event_id,
                    'success': False,
                    'error': str(e)
                })
                
        return results