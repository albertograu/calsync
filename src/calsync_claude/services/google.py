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
        """Create a new Google Calendar event, or update if it already exists."""
        self._ensure_authenticated()
        
        self.logger.info(f"🔍 Creating Google Calendar event: {event_data.summary}")
        self.logger.info(f"   → Original calendar ID: {calendar_id}")
        self.logger.info(f"   → Event UID: {event_data.uid}")
        self.logger.info(f"   → Event source: {event_data.source}")
        
        # Validate calendar ID first
        validated_calendar_id = await self._validate_calendar_id(calendar_id)
        self.logger.info(f"✅ Using validated calendar ID: {validated_calendar_id}")
        
        # With deterministic ID generation, we should rarely get duplicates
        # But keep a simple check for safety
        if event_data.uid:
            self.logger.debug(f"Creating event with UID: {event_data.uid}")
        
        # Proceed with creation using the validated ID
        return await self._create_event_with_retry(validated_calendar_id, event_data)
    
    # @retry(
    #     stop=stop_after_attempt(3),
    #     wait=wait_exponential(multiplier=1, min=4, max=10),
    #     retry=retry_if_exception_type((HttpError, CalendarServiceError))
    # )
    async def _create_event_with_retry(
        self,
        validated_calendar_id: str,
        event_data: CalendarEvent
    ) -> CalendarEvent:
        """Create event with retry logic using validated calendar ID."""
        try:
            # Convert event data WITH event ID generation to prevent duplicates
            # This follows Google's best practice: "generate your own unique event ID"
            google_event_data = self._convert_to_google_format(event_data, use_event_id=True)
            
            # Enhanced debugging for Google event payload
            self.logger.info(f"🔧 Google event payload for '{event_data.summary}':")
            self.logger.info(f"   → Summary: {google_event_data.get('summary')}")
            self.logger.info(f"   → Description length: {len(google_event_data.get('description', ''))}")
            self.logger.info(f"   → Location length: {len(google_event_data.get('location', ''))}")
            self.logger.info(f"   → Has custom ID: {'id' in google_event_data}")
            if 'id' in google_event_data:
                custom_id = google_event_data['id']
                self.logger.info(f"   → Custom ID: '{custom_id}' (length: {len(custom_id)})")
                self.logger.info(f"   → ID characters: {set(custom_id)}")
                # Validate against base32hex
                base32hex_chars = set('0123456789abcdefghijklmnopqrstuv')
                invalid_chars = set(custom_id) - base32hex_chars
                if invalid_chars:
                    self.logger.error(f"   ❌ INVALID CHARACTERS IN ID: {invalid_chars}")
                else:
                    self.logger.info(f"   ✅ ID uses only valid base32hex characters")
            self.logger.info(f"   → iCalUID: {google_event_data.get('iCalUID')}")
            self.logger.info(f"   → Start: {google_event_data.get('start')}")
            self.logger.info(f"   → End: {google_event_data.get('end')}")
            self.logger.info(f"   → Calendar ID: {validated_calendar_id}")
            self.logger.info(f"   → All fields: {list(google_event_data.keys())}")
            
            # Extra validation for recurringEventId if present
            if google_event_data.get('recurringEventId'):
                rec_id = google_event_data['recurringEventId']
                self.logger.info(f"   → recurringEventId: '{rec_id}'")
                rec_id_chars = set(rec_id) if rec_id else set()
                invalid_rec_chars = rec_id_chars - base32hex_chars
                if invalid_rec_chars:
                    self.logger.error(f"   ❌ INVALID CHARACTERS IN recurringEventId: {invalid_rec_chars}")
                    # Remove invalid recurringEventId to prevent error
                    google_event_data.pop('recurringEventId', None)
                    self.logger.warning(f"   🧹 Removed invalid recurringEventId to prevent API error")
            
            # Insert with deterministic ID generated from UID to prevent duplicates
            created_event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().insert(
                    calendarId=validated_calendar_id,
                    body=google_event_data
                ).execute()
            )
            
            return self._format_google_event(created_event)
            
        except HttpError as e:
            if e.resp.status == 409 and "duplicate" in str(e).lower():
                # 409 Duplicate - should be rare with deterministic IDs, but handle it
                self.logger.warning(f"🔄 409 Duplicate error despite deterministic ID - this indicates the event already exists")
                if event_data.uid:
                    try:
                        # Use the same compliant deterministic ID generation
                        deterministic_id = self._generate_compliant_event_id(event_data.uid)
                        self.logger.info(f"📝 Attempting to update existing event with compliant deterministic ID: {deterministic_id}")
                        return await self.update_event(validated_calendar_id, deterministic_id, event_data)
                    except Exception as update_error:
                        self.logger.error(f"Failed to update with deterministic ID: {update_error}")
                        # If that fails, the event might exist with a different ID, try UID search
                        try:
                            existing_events = await self._find_events_by_uid(validated_calendar_id, event_data.uid)
                            if existing_events:
                                self.logger.info(f"📝 Found existing event via UID search, updating instead")
                                return await self.update_event(validated_calendar_id, existing_events[0]['id'], event_data)
                        except Exception as search_error:
                            self.logger.error(f"UID search also failed: {search_error}")
            
            self.logger.error(f"❌ Create event failed with validated_calendar_id={validated_calendar_id}, error: {e}")
            raise CalendarServiceError(f"Failed to create Google event: {e}")
        except Exception as e:
            self.logger.error(f"❌ Create event failed with validated_calendar_id={validated_calendar_id}, error: {e}")
            raise CalendarServiceError(f"Failed to create Google event: {e}")
    
    async def _find_events_by_uid(self, calendar_id: str, uid: str) -> List[Dict[str, Any]]:
        """Find events in Google Calendar by iCalUID."""
        try:
            # Google Calendar API doesn't reliably support iCalUID parameter in list()
            # Instead, we need to search through events and filter manually
            # Get recent events and search through them
            events_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().list(
                    calendarId=calendar_id,
                    maxResults=250,  # Increased to catch more events
                    singleEvents=True,
                    orderBy='updated'
                ).execute()
            )
            
            events = events_result.get('items', [])
            
            # Filter events by iCalUID
            matching_events = []
            self.logger.debug(f"Searching through {len(events)} events for UID: {uid}")
            
            for event in events:
                event_uid = event.get('iCalUID')
                if event_uid == uid:
                    matching_events.append(event)
                    self.logger.debug(f"✅ Found matching event: {event.get('id')} with UID {event_uid}")
            
            self.logger.info(f"🔍 Search complete: Found {len(matching_events)} events with UID {uid} out of {len(events)} total events")
            return matching_events
            
        except HttpError as e:
            if e.resp.status == 404:
                return []  # Calendar not found, no events
            self.logger.warning(f"Failed to search for events by UID: {e}")
            return []  # Return empty instead of raising
        except Exception as e:
            self.logger.warning(f"Failed to search for events by UID: {e}")
            return []  # Return empty instead of raising

    async def _find_events_by_uid_thorough(self, calendar_id: str, uid: str) -> List[Dict[str, Any]]:
        """More thorough search for events by iCalUID - searches more events and time ranges."""
        try:
            all_events = []
            
            # Search recent events
            recent_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().list(
                    calendarId=calendar_id,
                    maxResults=500,  # Increased even more
                    singleEvents=True,
                    orderBy='updated'
                ).execute()
            )
            all_events.extend(recent_result.get('items', []))
            
            # Also search upcoming events
            from datetime import datetime, timedelta
            import pytz
            
            time_min = (datetime.now(pytz.UTC) - timedelta(days=90)).isoformat()
            time_max = (datetime.now(pytz.UTC) + timedelta(days=90)).isoformat()
            
            range_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().list(
                    calendarId=calendar_id,
                    maxResults=500,
                    singleEvents=True,
                    orderBy='startTime',
                    timeMin=time_min,
                    timeMax=time_max
                ).execute()
            )
            all_events.extend(range_result.get('items', []))
            
            # Remove duplicates based on event ID
            seen_ids = set()
            unique_events = []
            for event in all_events:
                if event.get('id') not in seen_ids:
                    unique_events.append(event)
                    seen_ids.add(event.get('id'))
            
            # Filter by iCalUID
            matching_events = []
            self.logger.info(f"🔍 Thorough search: Checking {len(unique_events)} unique events for UID: {uid}")
            
            for event in unique_events:
                event_uid = event.get('iCalUID')
                if event_uid == uid:
                    matching_events.append(event)
                    self.logger.info(f"✅ Found matching event in thorough search: {event.get('id')} with UID {event_uid}")
            
            self.logger.info(f"🔍 Thorough search complete: Found {len(matching_events)} events with UID {uid}")
            return matching_events
            
        except Exception as e:
            self.logger.warning(f"Failed to perform thorough search for events by UID: {e}")
            return []

    async def _find_events_by_content(self, calendar_id: str, event_data: CalendarEvent) -> List[Dict[str, Any]]:
        """Find events by matching content (summary, start time) when UID search fails."""
        try:
            # Search around the event's time (±1 day) for efficiency
            from datetime import timedelta
            import pytz
            
            start_time = event_data.start
            search_start = (start_time - timedelta(days=1)).isoformat()
            search_end = (start_time + timedelta(days=1)).isoformat()
            
            events_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().list(
                    calendarId=calendar_id,
                    timeMin=search_start,
                    timeMax=search_end,
                    singleEvents=True,
                    orderBy='startTime',
                    maxResults=100
                ).execute()
            )
            
            events = events_result.get('items', [])
            matching_events = []
            
            self.logger.debug(f"🔍 Content search: Found {len(events)} events in time range")
            
            for event in events:
                # Match by summary and start time (allowing small time differences)
                if (event.get('summary', '').strip().lower() == event_data.summary.strip().lower()):
                    
                    # Check start time match
                    event_start_str = None
                    if event_data.all_day:
                        event_start_str = event.get('start', {}).get('date')
                        expected_start = event_data.start.strftime('%Y-%m-%d')
                    else:
                        event_start_str = event.get('start', {}).get('dateTime', '').split('T')[0] if event.get('start', {}).get('dateTime') else None
                        expected_start = event_data.start.strftime('%Y-%m-%d')
                    
                    if event_start_str and event_start_str.startswith(expected_start):
                        matching_events.append(event)
                        self.logger.info(f"✅ Content match found: Event ID {event.get('id')} with summary '{event.get('summary')}' at {event_start_str}")
            
            self.logger.info(f"🔍 Content search complete: Found {len(matching_events)} content matches")
            return matching_events
            
        except Exception as e:
            self.logger.warning(f"Failed to perform content-based search: {e}")
            return []

    def _generate_compliant_event_id(self, uid: str) -> str:
        """Generate a Google Calendar-compliant event ID from a UID.

        Per Google Calendar API (Events resource) and RFC2938, event.id must use:
        - characters: [a-v0-9] (base32hex alphabet only)
        - length: 5..1024
        - recommend client-supplied stable IDs to avoid duplicates

        CRITICAL: Google Calendar strictly enforces base32hex format.
        Using characters outside [a-v0-9] causes "Invalid resource id value" errors.
        """
        import hashlib
        
        # Create hash from UID
        hash_bytes = hashlib.sha256(uid.encode()).digest()
        
        # STRICT RFC2938 base32hex alphabet - ONLY these characters are valid for Google Calendar
        base32hex_alphabet = '0123456789abcdefghijklmnopqrstuv'
        
        # Manual base32hex encoding ensuring strict compliance
        def base32hex_encode(data: bytes) -> str:
            """Encode bytes using RFC2938 base32hex alphabet."""
            bits = ''.join(format(byte, '08b') for byte in data)
            # Pad to multiple of 5 bits
            while len(bits) % 5 != 0:
                bits += '0'
            
            result = ''
            for i in range(0, len(bits), 5):
                chunk = bits[i:i+5]
                result += base32hex_alphabet[int(chunk, 2)]
            
            return result
        
        # Generate strictly compliant base32hex ID
        event_id = base32hex_encode(hash_bytes)
        
        # Truncate to reasonable length (Google allows up to 1024)
        if len(event_id) > 32:
            event_id = event_id[:32]
        
        # Ensure minimum length (Google requires at least 5)
        if len(event_id) < 5:
            event_id = event_id + '0' * (5 - len(event_id))
        
        # Ensure starts with a letter to avoid potential backend issues
        if event_id[0].isdigit():
            # Replace first digit with corresponding letter (0->a, 1->b, etc.)
            first_char = chr(ord('a') + int(event_id[0]))
            event_id = first_char + event_id[1:]
        
        self.logger.debug(f"Generated base32hex event ID: {event_id} (length: {len(event_id)}) from UID: {uid[:20]}...")
        return event_id

    async def _validate_calendar_id(self, calendar_id: str) -> str:
        """Validate Google Calendar ID efficiently without creating test events.
        
        Args:
            calendar_id: The calendar ID to validate
            
        Returns:
            Validated calendar ID
            
        Raises:
            CalendarServiceError: If calendar ID is invalid and no fallback available
        """
        self.logger.debug(f"🔍 Validating Google Calendar ID: {calendar_id}")
        
        try:
            # Simple validation: try to get calendar metadata (lightweight operation)
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.calendars().get(calendarId=calendar_id).execute()
            )
            
            self.logger.debug(f"✅ Calendar ID is valid: {calendar_id}")
            return calendar_id
            
        except HttpError as e:
            # Handle invalid calendar ID gracefully
            if e.resp.status == 400:
                self.logger.warning(f"📋 Google Calendar ID format invalid: {calendar_id}")
            elif e.resp.status == 404:
                self.logger.warning(f"📋 Google Calendar not found: {calendar_id}")
            elif e.resp.status == 403:
                self.logger.warning(f"📋 Google Calendar access denied: {calendar_id}")
            else:
                self.logger.warning(f"📋 Google Calendar validation failed: {e}")
            
            # Try to find a working alternative
            return await self._find_fallback_calendar()
            
        except Exception as e:
            self.logger.error(f"Unexpected error validating calendar ID {calendar_id}: {e}")
            return await self._find_fallback_calendar()
    
    async def _find_fallback_calendar(self) -> str:
        """Find a fallback Google Calendar when the configured one fails."""
        try:
            self.logger.info("🔍 Searching for fallback Google Calendar...")
            
            calendar_list = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.calendarList().list().execute()
            )
            
            # Look for primary calendar first
            for calendar_item in calendar_list.get('items', []):
                if calendar_item.get('primary', False):
                    primary_id = calendar_item['id']
                    self.logger.info(f"✅ Using primary calendar as fallback: {primary_id}")
                    return primary_id
            
            # Look for any writable calendar
            for calendar_item in calendar_list.get('items', []):
                access_role = calendar_item.get('accessRole', '')
                if access_role in ['owner', 'writer']:
                    fallback_id = calendar_item['id']
                    calendar_name = calendar_item.get('summary', 'Unknown')
                    self.logger.info(f"✅ Using writable calendar as fallback: {calendar_name} ({fallback_id})")
                    return fallback_id
            
            # Final fallback to 'primary' keyword
            self.logger.warning("⚠️  No suitable calendar found, using 'primary' keyword as last resort")
            return 'primary'
            
        except Exception as list_error:
            self.logger.error(f"Failed to list Google calendars for fallback: {list_error}")
            # Ultimate fallback
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
            # First, fetch the current event to get the latest sequence number
            current_event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.service.events().get(
                    calendarId=calendar_id,
                    eventId=event_id
                ).execute()
            )
            
            google_event_data = self._convert_to_google_format(event_data)
            
            # Use the current sequence number from the existing event
            # This prevents "Invalid sequence value" errors
            if 'sequence' in current_event:
                google_event_data['sequence'] = current_event['sequence']
            
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
        """Convert standard event format to Google Calendar format with validation."""
        
        # Sanitize text fields to prevent API errors
        def sanitize_text(text: str, max_length: int = None) -> str:
            if not text:
                return ''
            # Remove null bytes and other problematic characters
            sanitized = str(text).replace('\x00', '').strip()
            if max_length and len(sanitized) > max_length:
                sanitized = sanitized[:max_length-3] + '...'
            return sanitized
        
        google_event = {
            'summary': sanitize_text(event.summary, 1024),  # Google Calendar limit
            'description': sanitize_text(event.description, 8192),  # Google Calendar limit
            'location': sanitize_text(event.location, 1024)  # Google Calendar limit
        }
        
        # CRITICAL: Set custom event ID when we already have a UID to prevent duplicates.
        # IMPORTANT: Do NOT set a custom ID for recurrence override instances.
        # Google assigns unique IDs to override instances; forcing an ID can cause
        # "Invalid resource id value" or duplicate-ID conflicts.
        if use_event_id and event.uid:
            is_override = event.is_recurrence_override()
            self.logger.info(f"🔍 Event ID decision for '{event.summary}' (UID: {event.uid})")
            self.logger.info(f"   → Is recurrence override: {is_override}")
            self.logger.info(f"   → Recurrence overrides: {event.recurrence_overrides}")
            self.logger.info(f"   → Has recurring_event_id: {hasattr(event, 'recurring_event_id') and getattr(event, 'recurring_event_id', None)}")
            
            if not is_override:
                # Generate Google Calendar compliant ID from UID
                event_id = self._generate_compliant_event_id(event.uid)
                self.logger.info(f"🔧 Generated event ID '{event_id}' (length: {len(event_id)}) for UID: {event.uid}")
                google_event['id'] = event_id
            else:
                self.logger.info(f"⚠️  Skipping custom ID for recurrence override event: {event.summary}")
        elif use_event_id:
            self.logger.info(f"⚠️  Skipping event ID generation - missing UID for event: {event.summary}")
        
        # Set iCalUID for cross-platform matching
        if event.uid:
            # Ensure iCalUID is valid (no special characters that could cause issues)
            clean_uid = str(event.uid).strip()
            if clean_uid:
                google_event['iCalUID'] = clean_uid
                self.logger.debug(f"Set iCalUID: {clean_uid}")
            else:
                self.logger.warning(f"Event UID is empty after cleaning: '{event.uid}'")
        
        if event.all_day:
            # All-day event - ensure valid date format
            try:
                start_date = event.start.strftime('%Y-%m-%d')
                end_date = event.end.strftime('%Y-%m-%d')
                google_event['start'] = {'date': start_date}
                google_event['end'] = {'date': end_date}
                self.logger.debug(f"Set all-day dates: {start_date} to {end_date}")
            except Exception as e:
                self.logger.error(f"Failed to format all-day dates: {e}")
                raise CalendarServiceError(f"Invalid date format for all-day event: {e}")
        else:
            # Timed event - ensure valid datetime format
            try:
                start_dt = event.start.isoformat()
                end_dt = event.end.isoformat()
                google_event['start'] = {'dateTime': start_dt}
                google_event['end'] = {'dateTime': end_dt}
                self.logger.debug(f"Set timed datetimes: {start_dt} to {end_dt}")
            except Exception as e:
                self.logger.error(f"Failed to format datetime: {e}")
                raise CalendarServiceError(f"Invalid datetime format for timed event: {e}")
        
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
                    
                    # CRITICAL FIX: Set originalStartTime for Google Calendar exception events
                    # Google requires this field for recurrence exceptions
                    original_start = override.get('original_start') or override.get('recurrence_id')
                    if original_start:
                        try:
                            # Parse the original start time
                            from dateutil.parser import parse as parse_date
                            original_dt = parse_date(original_start)
                            
                            if event.all_day:
                                google_event['originalStartTime'] = {
                                    'date': original_dt.strftime('%Y-%m-%d')
                                }
                            else:
                                google_event['originalStartTime'] = {
                                    'dateTime': original_dt.isoformat()
                                }
                            self.logger.info(f"✅ Set originalStartTime for recurrence exception: {original_start}")
                        except Exception as e:
                            self.logger.warning(f"Failed to parse originalStartTime from {original_start}: {e}")
                    else:
                        self.logger.warning(f"Missing original start time for recurrence exception: {event.summary}")
        
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