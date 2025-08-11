"""Enhanced sync engine with conflict resolution and async operations."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, AsyncIterator, Set
from contextlib import asynccontextmanager

import pytz
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import Settings
from .database import DatabaseManager, EventMappingDB, SyncSessionDB, CalendarMappingDB
from .models import (
    CalendarEvent, EventSource, ConflictResolution, SyncOperation,
    SyncResult, SyncReport, SyncConfiguration, ChangeSet
)
from .services import GoogleCalendarService, iCloudCalendarService, CalendarServiceError
from .calendar_manager import CalendarManager

logger = logging.getLogger(__name__)


class ConflictResolver:
    """Handles conflict resolution between calendar events."""
    
    def __init__(self, strategy: ConflictResolution):
        """Initialize conflict resolver.
        
        Args:
            strategy: Conflict resolution strategy
        """
        self.strategy = strategy
        self.logger = logger.getChild('conflict_resolver')
    
    def resolve_conflict(
        self,
        google_event: CalendarEvent,
        icloud_event: CalendarEvent,
        mapping: EventMappingDB
    ) -> Tuple[Optional[CalendarEvent], str]:
        """Resolve conflict between two events using automated strategies for headless operation.
        
        Args:
            google_event: Google Calendar event
            icloud_event: iCloud Calendar event
            mapping: Event mapping from database
            
        Returns:
            Tuple of (winning_event, resolution_reason)
        """
        # For headless operation, never return None - always resolve automatically
        # Manual resolution is converted to LATEST_WINS
        strategy = self.strategy
        if strategy == ConflictResolution.MANUAL:
            strategy = ConflictResolution.LATEST_WINS
            self.logger.info("Converting MANUAL resolution to LATEST_WINS for headless operation")
        
        # First try sequence-based resolution (preferred for iCal events)
        google_seq = google_event.sequence or 0
        icloud_seq = icloud_event.sequence or 0
        
        if google_seq != icloud_seq:
            if google_seq > icloud_seq:
                return google_event, f"Auto-resolved: Google event has higher sequence ({google_seq} > {icloud_seq})"
            else:
                return icloud_event, f"Auto-resolved: iCloud event has higher sequence ({icloud_seq} > {google_seq})"
        
        # Sequence-based resolution failed, use configured strategy
        if strategy == ConflictResolution.LATEST_WINS:
            # Ensure both timestamps are timezone-aware for comparison
            google_updated = self._ensure_timezone_aware(google_event.updated)
            icloud_updated = self._ensure_timezone_aware(icloud_event.updated)
            
            if google_updated > icloud_updated:
                return google_event, f"Auto-resolved: Google event is more recent ({google_updated} > {icloud_updated})"
            elif icloud_updated > google_updated:
                return icloud_event, f"Auto-resolved: iCloud event is more recent ({icloud_updated} > {google_updated})"
            else:
                # Same timestamp, prefer Google as tiebreaker for consistency
                return google_event, "Auto-resolved: Equal timestamps, Google wins (tiebreaker)"
        
        elif strategy == ConflictResolution.GOOGLE_WINS:
            return google_event, "Auto-resolved: Google wins policy"
        
        elif strategy == ConflictResolution.ICLOUD_WINS:
            return icloud_event, "Auto-resolved: iCloud wins policy"
        
        else:
            # Fallback for unknown strategies - prefer latest
            google_updated = self._ensure_timezone_aware(google_event.updated)
            icloud_updated = self._ensure_timezone_aware(icloud_event.updated)
            if google_updated > icloud_updated:
                return google_event, f"Auto-resolved: Unknown strategy '{strategy}', defaulted to latest (Google)"
            else:
                return icloud_event, f"Auto-resolved: Unknown strategy '{strategy}', defaulted to latest (iCloud)"
    
    def _ensure_timezone_aware(self, dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware for safe comparison.
        
        Args:
            dt: Datetime object that may be timezone-naive or timezone-aware
            
        Returns:
            Timezone-aware datetime (assumes UTC for naive datetimes)
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


class SyncEngine:
    """Enhanced synchronization engine with async operations and conflict resolution."""
    
    def __init__(self, settings: Settings):
        """Initialize sync engine.
        
        Args:
            settings: Application settings
        """
        self.settings = settings
        self.db_manager = DatabaseManager(settings)
        self.google_service = GoogleCalendarService(settings)
        self.icloud_service = iCloudCalendarService(settings)
        self.conflict_resolver = ConflictResolver(settings.sync_config.conflict_resolution)
        self.calendar_manager = CalendarManager(
            settings, self.google_service, self.icloud_service, self.db_manager
        )
        self.logger = logger.getChild('sync_engine')
        self._services_authenticated = False
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()
    
    async def initialize(self) -> None:
        """Initialize the sync engine."""
        # Initialize database
        self.db_manager.init_db()
        
        # Authenticate services
        await self._authenticate_services()
        
        self.logger.info("Sync engine initialized successfully")
    
    async def cleanup(self) -> None:
        """Clean up resources."""
        if hasattr(self.google_service, 'close'):
            await self.google_service.close()
        
        self.logger.info("Sync engine cleaned up")
    
    async def _authenticate_services(self) -> None:
        """Authenticate both calendar services."""
        try:
            await asyncio.gather(
                self.google_service.authenticate(),
                self.icloud_service.authenticate()
            )
            self._services_authenticated = True
            self.logger.info("Successfully authenticated with both calendar services")
        except Exception as e:
            self.logger.error(f"Failed to authenticate services: {e}")
            raise
    
    async def test_connections(self) -> Dict[str, Any]:
        """Test connections to both calendar services.
        
        Returns:
            Dictionary with connection test results
        """
        if not self._services_authenticated:
            await self._authenticate_services()
        
        google_result, icloud_result = await asyncio.gather(
            self.google_service.test_connection(),
            self.icloud_service.test_connection(),
            return_exceptions=True
        )
        
        return {
            'google': google_result if not isinstance(google_result, Exception) else {
                'success': False,
                'error': str(google_result),
                'error_type': type(google_result).__name__
            },
            'icloud': icloud_result if not isinstance(icloud_result, Exception) else {
                'success': False,
                'error': str(icloud_result),
                'error_type': type(icloud_result).__name__
            }
        }
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(CalendarServiceError)
    )
    async def sync_calendars(self, dry_run: bool = False) -> SyncReport:
        """Perform comprehensive two-way calendar synchronization.
        
        Args:
            dry_run: If True, don't make actual changes
            
        Returns:
            Sync report with detailed results
        """
        if not self._services_authenticated:
            await self._authenticate_services()
        
        # Create sync session
        with self.db_manager.get_session() as session:
            sync_session = self.db_manager.create_sync_session(session, dry_run=dry_run)
            sync_report = SyncReport(
                sync_id=sync_session.id,
                started_at=sync_session.started_at,
                dry_run=dry_run
            )
        
        try:
            self.logger.info(f"Starting sync session {sync_session.id} (dry_run={dry_run})")
            
            # Get or create calendar mappings
            calendar_mappings = await self._get_or_create_calendar_mappings()
            
            if not calendar_mappings:
                self.logger.warning("No calendar mappings found or configured")
                raise Exception("No calendar mappings available for synchronization")
            
            self.logger.info(f"Syncing {len(calendar_mappings)} calendar pairs")
            
            # Perform bidirectional sync for each mapped calendar pair
            for mapping in calendar_mappings:
                await self._sync_calendar_pair(
                    mapping.google_calendar_id,
                    mapping.icloud_calendar_id, 
                    mapping,
                    sync_session, 
                    sync_report, 
                    dry_run
                )
            
            # Complete sync session
            with self.db_manager.get_session() as session:
                self.db_manager.complete_sync_session(session, sync_session, status='completed')
            
            sync_report.completed_at = datetime.now(pytz.UTC)
            self.logger.info(f"Sync session {sync_session.id} completed successfully")
            
        except Exception as e:
            # Mark sync session as failed
            with self.db_manager.get_session() as session:
                self.db_manager.complete_sync_session(
                    session, sync_session, status='failed', error_message=str(e)
                )
            
            sync_report.errors.append(str(e))
            sync_report.completed_at = datetime.now(pytz.UTC)
            self.logger.error(f"Sync session {sync_session.id} failed: {e}")
            raise
        
        return sync_report
    
    async def _get_or_create_calendar_mappings(self) -> List[CalendarMappingDB]:
        """Get calendar mappings from database or create them from configuration.
        
        Returns:
            List of calendar mappings ready for sync
        """
        # First try to get existing mappings from database
        existing_mappings = await self.calendar_manager.get_all_mappings()
        
        if existing_mappings:
            self.logger.info(f"Using {len(existing_mappings)} existing calendar mappings")
            return existing_mappings
        
        # No existing mappings, discover calendars and create mappings
        self.logger.info("No existing mappings found, discovering calendars...")
        
        google_calendars, icloud_calendars = await self.calendar_manager.discover_calendars()
        
        # Auto-match calendars
        match_result = await self.calendar_manager.auto_match_calendars(
            google_calendars, icloud_calendars
        )
        
        if not match_result.matched_pairs:
            # No matches found, create default mapping (primary to primary)
            primary_google = next((c for c in google_calendars if c.is_primary), None)
            primary_icloud = next((c for c in icloud_calendars if c.is_primary), None)
            
            if primary_google and primary_icloud:
                match_result.matched_pairs = [(primary_google, primary_icloud)]
                self.logger.info("Created default primary-to-primary mapping")
            else:
                raise Exception("No calendar matches found and unable to create default mapping")
        
        # Create database mappings
        calendar_mappings = await self.calendar_manager.create_calendar_mappings(
            match_result.matched_pairs
        )
        
        self.logger.info(f"Created {len(calendar_mappings)} new calendar mappings")
        
        # Report unmatched calendars
        if match_result.unmatched_google:
            self.logger.info(
                f"Unmatched Google calendars: {[c.name for c in match_result.unmatched_google]}"
            )
        if match_result.unmatched_icloud:
            self.logger.info(
                f"Unmatched iCloud calendars: {[c.name for c in match_result.unmatched_icloud]}"
            )
        
        return calendar_mappings

    async def _sync_calendar_pair(
        self,
        google_calendar_id: str,
        icloud_calendar_id: str,
        calendar_mapping: CalendarMappingDB,
        sync_session: SyncSessionDB,
        sync_report: SyncReport,
        dry_run: bool
    ) -> None:
        """Sync a specific pair of calendars bidirectionally.
        
        Args:
            google_calendar_id: Google calendar ID
            icloud_calendar_id: iCloud calendar ID
            calendar_mapping: Calendar mapping from database
            sync_session: Database sync session
            sync_report: Sync report to update
            dry_run: Whether this is a dry run
        """
        self.logger.info(
            f"Syncing calendar pair: {calendar_mapping.google_calendar_name or google_calendar_id} "
            f"<-> {calendar_mapping.icloud_calendar_name or icloud_calendar_id}"
        )
        
        # Skip if mapping is disabled
        if not calendar_mapping.enabled:
            self.logger.info("Calendar mapping is disabled, skipping")
            return
        
        # Calculate time range for sync
        now = datetime.now(pytz.UTC)
        time_min = now - timedelta(days=self.settings.sync_config.sync_past_days)
        time_max = now + timedelta(days=self.settings.sync_config.sync_future_days)
        
        # Get last sync time for incremental sync
        last_sync_time = None
        with self.db_manager.get_session() as session:
            last_session = session.query(SyncSessionDB).filter(
                SyncSessionDB.calendar_mapping_id == calendar_mapping.id,
                SyncSessionDB.status == 'completed'
            ).order_by(SyncSessionDB.completed_at.desc()).first()
            
            if last_session:
                last_sync_time = last_session.completed_at
        
        # Get change sets from both calendars
        google_events: Dict[str, CalendarEvent] = {}
        icloud_events: Dict[str, CalendarEvent] = {}
        google_events_by_uid: Dict[str, CalendarEvent] = {}
        icloud_events_by_uid: Dict[str, CalendarEvent] = {}
        google_deleted_ids: set[str] = set()
        icloud_deleted_raw: set[str] = set()  # May contain hrefs; map below

        google_sync_token = calendar_mapping.google_sync_token
        icloud_sync_token = calendar_mapping.icloud_sync_token

        # CRITICAL FIX: Acquire initial sync tokens if missing
        # This enables bidirectional sync and deletion detection
        self.logger.info(f"🔍 SYNC TOKEN STATUS CHECK:")
        self.logger.info(f"  📅 Calendar Mapping ID: {calendar_mapping.id}")
        self.logger.info(f"  🟢 Google Calendar: {calendar_mapping.google_calendar_name or 'Unknown'} ({google_calendar_id})")
        self.logger.info(f"  🍎 iCloud Calendar: {calendar_mapping.icloud_calendar_name or 'Unknown'} ({icloud_calendar_id})")
        self.logger.info(f"  🔑 Google Sync Token: {'✅ EXISTS' if google_sync_token else '❌ MISSING'}")
        self.logger.info(f"  🔑 iCloud Sync Token: {'✅ EXISTS' if icloud_sync_token else '❌ MISSING'}")
        
        if not google_sync_token:
            self.logger.info(f"🚀 ACQUIRING GOOGLE SYNC TOKEN for calendar {google_calendar_id}")
            try:
                google_sync_token = await self.google_service.get_sync_token(google_calendar_id)
                self.logger.info(f"✅ Google sync token acquired successfully: {google_sync_token[:50]}...")
                self.logger.info("🎯 Google incremental sync now ENABLED")
            except Exception as e:
                self.logger.error(f"❌ Failed to acquire Google sync token: {type(e).__name__}: {e}")
                self.logger.warning("⚠️  Falling back to time-window sync for Google (no deletion detection)")
                google_sync_token = None
        else:
            self.logger.info(f"🔄 Using existing Google sync token: {google_sync_token[:50]}...")
        
        if not icloud_sync_token:
            self.logger.info(f"🚀 ACQUIRING ICLOUD SYNC TOKEN for calendar {icloud_calendar_id}")
            try:
                icloud_sync_token = await self.icloud_service.get_sync_token(icloud_calendar_id)
                self.logger.info(f"✅ iCloud sync token acquired successfully: {icloud_sync_token[:50] if len(str(icloud_sync_token)) > 50 else icloud_sync_token}")
                self.logger.info("🎯 iCloud incremental sync now ENABLED")
            except Exception as e:
                self.logger.error(f"❌ Failed to acquire iCloud sync token: {type(e).__name__}: {e}")
                self.logger.warning("⚠️  Falling back to time-window sync for iCloud (no deletion detection)")
                icloud_sync_token = None
        else:
            self.logger.info(f"🔄 Using existing iCloud sync token: {icloud_sync_token[:50] if len(str(icloud_sync_token)) > 50 else icloud_sync_token}...")
        
        self.logger.info(f"🔐 FINAL TOKEN STATUS: Google={'✅' if google_sync_token else '❌'} | iCloud={'✅' if icloud_sync_token else '❌'}")
        if google_sync_token and icloud_sync_token:
            self.logger.info("🎉 BIDIRECTIONAL SYNC WITH DELETION DETECTION ENABLED!")
        elif google_sync_token or icloud_sync_token:
            self.logger.info("⚡ PARTIAL INCREMENTAL SYNC ENABLED (limited deletion detection)")
        else:
            self.logger.warning("⚠️  TIME-WINDOW SYNC ONLY (no reliable deletion detection)")

        new_google_sync_token: Optional[str] = None
        new_icloud_sync_token: Optional[str] = None

        # Fetch Google change set with proper error handling
        g_cs = await self._fetch_google_change_set_with_retry(
            google_calendar_id, google_sync_token, time_min, time_max, 
            last_sync_time, calendar_mapping
        )

        google_events = dict(g_cs.changed)
        google_deleted_ids = set(g_cs.deleted_native_ids)
        new_google_sync_token = g_cs.next_sync_token
        for ev in google_events.values():
            if ev.uid:
                google_events_by_uid[ev.uid] = ev

        # Fetch iCloud change set
        i_cs: ChangeSet[CalendarEvent] = await self.icloud_service.get_change_set(
            icloud_calendar_id,
            time_min=None if icloud_sync_token else time_min,
            time_max=None if icloud_sync_token else time_max,
            max_results=self.settings.sync_config.max_events_per_sync,
            updated_min=None if icloud_sync_token else last_sync_time,
            sync_token=icloud_sync_token
        )
        icloud_events = dict(i_cs.changed)
        icloud_deleted_raw = set(i_cs.deleted_native_ids)
        new_icloud_sync_token = i_cs.next_sync_token
        
        # Check if we need to clear an invalid sync token
        if hasattr(i_cs, 'invalid_token_used') and i_cs.invalid_token_used:
            self.logger.warning(f"🧹 Clearing invalid iCloud sync token from database: {i_cs.invalid_token_used}")
            calendar_mapping.icloud_sync_token = None
            with self.db_manager.get_session() as session:
                session.merge(calendar_mapping)
                session.commit()
        for ev in icloud_events.values():
            if ev.uid:
                icloud_events_by_uid[ev.uid] = ev

        # Persist new tokens (including initially acquired ones)
        self.logger.info("🔍 SYNC TOKEN PERSISTENCE CHECK:")
        self.logger.info(f"  🆕 New Google token from API: {'✅' if new_google_sync_token else '❌'}")
        self.logger.info(f"  🆕 New iCloud token from API: {'✅' if new_icloud_sync_token else '❌'}")
        self.logger.info(f"  🔄 Google token changed: {'✅' if (google_sync_token != calendar_mapping.google_sync_token) else '❌'}")
        self.logger.info(f"  🔄 iCloud token changed: {'✅' if (icloud_sync_token != calendar_mapping.icloud_sync_token) else '❌'}")
        
        tokens_to_save = (
            new_google_sync_token or new_icloud_sync_token or 
            (google_sync_token != calendar_mapping.google_sync_token) or
            (icloud_sync_token != calendar_mapping.icloud_sync_token)
        )
        
        if tokens_to_save:
            self.logger.info("💾 SAVING SYNC TOKENS TO DATABASE...")
            try:
                with self.db_manager.get_session() as session:
                    # Refresh the mapping object in this session
                    mapping = session.merge(calendar_mapping)
                    self.logger.info(f"🔄 Database: Merged calendar mapping ID {mapping.id}")
                    
                    # Save Google token (either new from API or initially acquired)
                    if new_google_sync_token:
                        old_token = mapping.google_sync_token
                        mapping.google_sync_token = new_google_sync_token
                        mapping.google_last_updated = datetime.now(pytz.UTC)
                        self.logger.info(f"💾 Database: Updated Google sync token (from API response)")
                        self.logger.info(f"  📊 Old: {old_token[:50] if old_token else 'None'}...")
                        self.logger.info(f"  📊 New: {new_google_sync_token[:50]}...")
                    elif google_sync_token != calendar_mapping.google_sync_token:
                        # Initially acquired token
                        old_token = mapping.google_sync_token
                        mapping.google_sync_token = google_sync_token
                        mapping.google_last_updated = datetime.now(pytz.UTC)
                        self.logger.info(f"💾 Database: Saved initial Google sync token")
                        self.logger.info(f"  📊 Old: {old_token[:50] if old_token else 'None'}...")
                        self.logger.info(f"  📊 New: {google_sync_token[:50]}...")
                    
                    # Save iCloud token (either new from API or initially acquired)  
                    if new_icloud_sync_token:
                        old_token = mapping.icloud_sync_token
                        mapping.icloud_sync_token = new_icloud_sync_token
                        mapping.icloud_last_updated = datetime.now(pytz.UTC)
                        self.logger.info(f"💾 Database: Updated iCloud sync token (from API response)")
                        self.logger.info(f"  📊 Old: {old_token if old_token else 'None'}")
                        self.logger.info(f"  📊 New: {new_icloud_sync_token}")
                    elif icloud_sync_token != calendar_mapping.icloud_sync_token:
                        # Initially acquired token
                        old_token = mapping.icloud_sync_token
                        mapping.icloud_sync_token = icloud_sync_token
                        mapping.icloud_last_updated = datetime.now(pytz.UTC)
                        self.logger.info(f"💾 Database: Saved initial iCloud sync token")
                        self.logger.info(f"  📊 Old: {old_token if old_token else 'None'}")
                        self.logger.info(f"  📊 New: {icloud_sync_token}")
                    
                    session.commit()
                    self.logger.info("✅ Database: Sync tokens committed successfully")
                    
                    # Update the in-memory object
                    calendar_mapping.google_sync_token = mapping.google_sync_token
                    calendar_mapping.icloud_sync_token = mapping.icloud_sync_token
                    self.logger.info("🔄 Memory: In-memory calendar mapping updated")
                    
            except Exception as e:
                self.logger.error(f"❌ Database: Failed to save sync tokens: {type(e).__name__}: {e}")
                raise
        else:
            self.logger.info("⏭️  No sync tokens to save - all up to date")

        self.logger.info(
            f"Change sets: Google changed={len(google_events)} deleted={len(google_deleted_ids)}; "
            f"iCloud changed={len(icloud_events)} deleted_raw={len(icloud_deleted_raw)}"
        )
        
        # Get existing event mappings for this calendar pair
        with self.db_manager.get_session() as session:
            existing_mappings = session.query(EventMappingDB).filter(
                EventMappingDB.calendar_mapping_id == calendar_mapping.id
            ).all()
            # Expunge all objects from session so they can be used outside the session
            for mapping in existing_mappings:
                session.expunge(mapping)
        
        mappings_by_google = {m.google_event_id: m for m in existing_mappings if m.google_event_id}
        mappings_by_icloud = {m.icloud_event_id: m for m in existing_mappings if m.icloud_event_id}
        
        # Track processed events
        processed_google = set()
        processed_icloud = set()
        
        # CRITICAL: Group recurrence overrides with master events before syncing
        google_events_grouped = self._group_recurrence_events(google_events)
        icloud_events_grouped = self._group_recurrence_events(icloud_events)
        
        # Check sync direction and perform appropriate syncs with UID-based deduplication
        if calendar_mapping.bidirectional or calendar_mapping.sync_direction == 'google_to_icloud':
            # Process Google -> iCloud sync with recurrence grouping
            for group_id, group_data in google_events_grouped.items():
                if group_id in processed_google:
                    continue
                
                master_event = group_data['master']
                override_events = group_data['overrides']
                
                # Sync master event first
                if master_event.should_sync_to_calendar(icloud_calendar_id, icloud_events):
                    await self._sync_event_to_target(
                        master_event, EventSource.ICLOUD, icloud_calendar_id,
                        calendar_mapping, mappings_by_google, sync_session, sync_report, dry_run,
                        target_events_by_uid=icloud_events_by_uid
                    )
                processed_google.add(master_event.id)
                
                # Sync override events
                for override_event in override_events:
                    if override_event.id not in processed_google:
                        if override_event.should_sync_to_calendar(icloud_calendar_id, icloud_events):
                            await self._sync_event_to_target(
                                override_event, EventSource.ICLOUD, icloud_calendar_id,
                                calendar_mapping, mappings_by_google, sync_session, sync_report, dry_run,
                                target_events_by_uid=icloud_events_by_uid
                            )
                        processed_google.add(override_event.id)
        
        if calendar_mapping.bidirectional or calendar_mapping.sync_direction == 'icloud_to_google':
            # Process iCloud -> Google sync with recurrence grouping
            for group_id, group_data in icloud_events_grouped.items():
                if group_id in processed_icloud:
                    continue
                
                master_event = group_data['master']
                override_events = group_data['overrides']
                
                # Sync master event first
                if master_event.should_sync_to_calendar(google_calendar_id, google_events):
                    await self._sync_event_to_target(
                        master_event, EventSource.GOOGLE, google_calendar_id,
                        calendar_mapping, mappings_by_icloud, sync_session, sync_report, dry_run,
                        target_events_by_uid=google_events_by_uid
                    )
                processed_icloud.add(master_event.id)
                
                # Sync override events
                for override_event in override_events:
                    if override_event.id not in processed_icloud:
                        if override_event.should_sync_to_calendar(google_calendar_id, google_events):
                            await self._sync_event_to_target(
                                override_event, EventSource.GOOGLE, google_calendar_id,
                                calendar_mapping, mappings_by_icloud, sync_session, sync_report, dry_run,
                                target_events_by_uid=google_events_by_uid
                            )
                        processed_icloud.add(override_event.id)
        
        # Translate iCloud deleted hrefs to event IDs using improved mapping logic
        icloud_deleted_ids: set[str] = set()
        if icloud_deleted_raw:
            icloud_deleted_ids = await self._map_icloud_hrefs_to_event_ids(
                icloud_deleted_raw, existing_mappings, icloud_calendar_id
            )

        # Handle deletions using explicit deleted_id sets, only if used_sync_token on that side
        await self._handle_deletions(
            google_deleted_ids if g_cs.used_sync_token else set(),
            icloud_deleted_ids if i_cs.used_sync_token else set(),
            existing_mappings,
            google_calendar_id, icloud_calendar_id, calendar_mapping,
            sync_session, sync_report, dry_run
        )
    
    async def _sync_event_to_target(
        self,
        source_event: CalendarEvent,
        target_source: EventSource,
        target_calendar_id: str,
        calendar_mapping: CalendarMappingDB,
        mappings: Dict[str, EventMappingDB],
        sync_session: SyncSessionDB,
        sync_report: SyncReport,
        dry_run: bool,
        target_events_by_uid: Optional[Dict[str, CalendarEvent]] = None
    ) -> None:
        """Sync a single event to the target service.
        
        Args:
            source_event: Source event to sync
            target_source: Target service
            target_calendar_id: Target calendar ID
            calendar_mapping: Calendar mapping from database
            mappings: Existing event mappings
            sync_session: Database sync session
            sync_report: Sync report to update
            dry_run: Whether this is a dry run
            target_events_by_uid: Target events indexed by UID
        """
        try:
            target_service = (
                self.icloud_service if target_source == EventSource.ICLOUD 
                else self.google_service
            )
            
            mapping = mappings.get(source_event.id)
            content_hash = source_event.content_hash()
            
            if mapping:
                # Check if content has changed
                if mapping.content_hash == content_hash:
                    # No changes, skip
                    await self._record_sync_operation(
                        sync_session, sync_report, SyncOperation.SKIP,
                        source_event.source, target_source, source_event.id,
                        source_event.summary, True, mapping=mapping
                    )
                    return
                
                # Update existing event
                target_event_id = (
                    mapping.icloud_event_id if target_source == EventSource.ICLOUD
                    else mapping.google_event_id
                )
                
                if target_event_id:
                    # Check for conflicts
                    try:
                        existing_target_event = await target_service.get_event(
                            target_calendar_id, target_event_id
                        )
                        
                        # Detect conflicts
                        if await self._detect_conflict(source_event, existing_target_event, mapping):
                            await self._handle_conflict(
                                source_event, existing_target_event, mapping,
                                target_calendar_id, sync_session, sync_report, dry_run
                            )
                            return
                        
                        # Update event
                        if not dry_run:
                            updated_event = await target_service.update_event(
                                target_calendar_id, target_event_id, source_event
                            )
                            
                            # Update mapping
                            with self.db_manager.get_session() as session:
                                self.db_manager.update_event_mapping(
                                    session, mapping,
                                    content_hash=content_hash,
                                    sync_direction=f"{source_event.source.value}_to_{target_source.value}"
                                )
                        
                        await self._record_sync_operation(
                            sync_session, sync_report, SyncOperation.UPDATE,
                            source_event.source, target_source, source_event.id,
                            source_event.summary, True, mapping=mapping
                        )
                        
                    except Exception as e:
                        await self._record_sync_operation(
                            sync_session, sync_report, SyncOperation.UPDATE,
                            source_event.source, target_source, source_event.id,
                            source_event.summary, False, error=str(e), mapping=mapping
                        )
            else:
                # Create new event
                if not dry_run:
                    created_event = await target_service.create_event(
                        target_calendar_id, source_event
                    )
                    
                    # Extract calendar mapping values before new session to avoid DetachedInstanceError
                    calendar_mapping_id = calendar_mapping.id
                    google_calendar_id = calendar_mapping.google_calendar_id
                    icloud_calendar_id = calendar_mapping.icloud_calendar_id
                    
                    # Create mapping with all necessary fields for production
                    with self.db_manager.get_session() as session:
                        if source_event.source == EventSource.GOOGLE:
                            # Extract resource info from created iCloud event
                            icloud_resource_url = None
                            if hasattr(created_event, 'original_data'):
                                icloud_resource_url = created_event.original_data.get('resource_url')
                            
                            mapping = EventMappingDB(
                                calendar_mapping_id=calendar_mapping_id,
                                google_event_id=source_event.id,
                                icloud_event_id=created_event.id,
                                google_calendar_id=google_calendar_id,
                                icloud_calendar_id=icloud_calendar_id,
                                
                                # UIDs for cross-platform matching (CRITICAL)
                                google_ical_uid=source_event.uid,
                                icloud_uid=created_event.uid,
                                event_uid=source_event.uid or created_event.uid,
                                
                                # Resource paths for direct access
                                icloud_resource_url=icloud_resource_url,
                                google_self_link=source_event.original_data.get('selfLink') if source_event.original_data else None,
                                
                                # ETags and sequences
                                google_etag=source_event.etag,
                                icloud_etag=created_event.etag,
                                google_sequence=source_event.sequence or 0,
                                icloud_sequence=created_event.sequence or 0,
                                
                                content_hash=content_hash,
                                sync_direction=f"{source_event.source.value}_to_{target_source.value}",
                                last_sync_at=datetime.now(pytz.UTC),
                                sync_status='active'
                            )
                        else:
                            # Extract resource info from created Google event  
                            google_self_link = None
                            if hasattr(created_event, 'original_data'):
                                google_self_link = created_event.original_data.get('selfLink')
                                
                            mapping = EventMappingDB(
                                calendar_mapping_id=calendar_mapping_id,
                                google_event_id=created_event.id,
                                icloud_event_id=source_event.id,
                                google_calendar_id=google_calendar_id,
                                icloud_calendar_id=icloud_calendar_id,
                                
                                # UIDs for cross-platform matching (CRITICAL)
                                google_ical_uid=created_event.uid,
                                icloud_uid=source_event.uid,
                                event_uid=source_event.uid or created_event.uid,
                                
                                # Resource paths for direct access
                                icloud_resource_url=source_event.original_data.get('resource_url') if source_event.original_data else None,
                                google_self_link=google_self_link,
                                
                                # ETags and sequences
                                google_etag=created_event.etag,
                                icloud_etag=source_event.etag,
                                google_sequence=created_event.sequence or 0,
                                icloud_sequence=source_event.sequence or 0,
                                
                                content_hash=content_hash,
                                sync_direction=f"{source_event.source.value}_to_{target_source.value}",
                                last_sync_at=datetime.now(pytz.UTC),
                                sync_status='active'
                            )
                        
                        session.add(mapping)
                        session.commit()
                
                await self._record_sync_operation(
                    sync_session, sync_report, SyncOperation.CREATE,
                    source_event.source, target_source, source_event.id,
                    source_event.summary, True, mapping=mapping
                )
                
        except Exception as e:
            # Enhanced error logging to see actual root cause
            error_details = str(e)
            error_type = type(e).__name__
            
            # Unwrap RetryError to get the real error
            if "RetryError" in error_type and hasattr(e, 'last_attempt') and e.last_attempt:
                try:
                    real_error = e.last_attempt.exception()
                    if real_error:
                        error_details = f"{type(real_error).__name__}: {str(real_error)}"
                        error_type = type(real_error).__name__
                except:
                    pass
            
            self.logger.error(f"❌ Failed to sync event {source_event.id}: {error_type}: {error_details}")
            self.logger.error(f"   📝 Event: '{source_event.summary}' ({source_event.source.value} → {target_source.value})")
            
            await self._record_sync_operation(
                sync_session, sync_report, SyncOperation.CREATE if not mapping else SyncOperation.UPDATE,
                source_event.source, target_source, source_event.id,
                source_event.summary, False, error=str(e)
            )
    
    async def _detect_conflict(
        self,
        source_event: CalendarEvent,
        target_event: CalendarEvent,
        mapping: EventMappingDB
    ) -> bool:
        """Detect if there's a conflict between events.
        
        Args:
            source_event: Source event
            target_event: Target event
            mapping: Event mapping
            
        Returns:
            True if conflict detected
        """
        # Check if both events have been modified since last sync
        if mapping.last_sync_at:
            # Ensure timezone-aware comparison
            source_updated = self._ensure_timezone_aware(source_event.updated)
            target_updated = self._ensure_timezone_aware(target_event.updated)
            last_sync = self._ensure_timezone_aware(mapping.last_sync_at)
            
            source_modified = source_updated > last_sync
            target_modified = target_updated > last_sync
            
            if source_modified and target_modified:
                # Both modified - potential conflict
                source_hash = source_event.content_hash()
                target_hash = target_event.content_hash()
                return source_hash != target_hash
        
        return False
    
    async def _handle_conflict(
        self,
        source_event: CalendarEvent,
        target_event: CalendarEvent,
        mapping: EventMappingDB,
        target_calendar_id: str,
        sync_session: SyncSessionDB,
        sync_report: SyncReport,
        dry_run: bool
    ) -> None:
        """Handle conflict between events with automated resolution for headless operation.
        
        Args:
            source_event: Source event
            target_event: Target event
            mapping: Event mapping
            target_calendar_id: Target calendar ID
            sync_session: Sync session
            sync_report: Sync report
            dry_run: Whether this is a dry run
        """
        self.logger.warning(
            f"Conflict detected for events {source_event.id} <-> {target_event.id} "
            f"(source: {source_event.source.value}, target: {target_event.source.value})"
        )
        
        # Enhanced conflict resolution for headless operation
        winning_event, reason = self.conflict_resolver.resolve_conflict(
            source_event if source_event.source == EventSource.GOOGLE else target_event,
            target_event if target_event.source == EventSource.ICLOUD else source_event,
            mapping
        )
        
        if winning_event:
            # Apply automated resolution
            target_service = (
                self.icloud_service if target_event.source == EventSource.ICLOUD
                else self.google_service
            )
            
            if not dry_run:
                try:
                    await target_service.update_event(
                        target_calendar_id, target_event.id, winning_event
                    )
                    
                    # Update mapping with resolution info
                    with self.db_manager.get_session() as session:
                        self.db_manager.update_event_mapping(
                            session, mapping,
                            content_hash=winning_event.content_hash(),
                            sync_direction=f"{winning_event.source.value}_wins_conflict_resolution"
                        )
                    
                    self.logger.info(f"✅ Conflict auto-resolved: {reason}")
                    
                    await self._record_sync_operation(
                        sync_session, sync_report, SyncOperation.UPDATE,
                        source_event.source, target_event.source, source_event.id,
                        source_event.summary, True, mapping=mapping
                    )
                    
                except Exception as e:
                    self.logger.error(f"Failed to apply conflict resolution: {e}")
                    # Fall through to conflict logging
                    winning_event = None
        
        if not winning_event:
            # Log conflict for monitoring but don't create database entries for headless operation
            conflict_details = {
                'google_event_id': source_event.id if source_event.source == EventSource.GOOGLE else target_event.id,
                'icloud_event_id': target_event.id if target_event.source == EventSource.ICLOUD else source_event.id,
                'google_summary': source_event.summary if source_event.source == EventSource.GOOGLE else target_event.summary,
                'icloud_summary': target_event.summary if target_event.source == EventSource.ICLOUD else source_event.summary,
                'google_updated': str(source_event.updated if source_event.source == EventSource.GOOGLE else target_event.updated),
                'icloud_updated': str(target_event.updated if target_event.source == EventSource.ICLOUD else source_event.updated),
                'conflict_reason': reason or 'Unable to auto-resolve'
            }
            
            # Log structured conflict data for monitoring systems
            self.logger.error(
                f"❌ Unresolved conflict requiring attention",
                extra={
                    'conflict_type': 'content_mismatch',
                    'conflict_details': conflict_details,
                    'sync_session_id': str(sync_session.id) if sync_session else None
                }
            )
            
            # For headless operation, we'll skip the conflicted event rather than create database conflicts
            # This prevents the sync from getting stuck on unresolvable conflicts
            sync_report.conflicts.append({
                'source_event_id': source_event.id,
                'target_event_id': target_event.id,
                'reason': reason or 'Unable to auto-resolve',
                'resolution': 'skipped_for_headless_operation',
                'details': conflict_details
            })
            
            await self._record_sync_operation(
                sync_session, sync_report, SyncOperation.SKIP,
                source_event.source, target_event.source, source_event.id,
                source_event.summary, False, 
                error=f"Conflict skipped: {reason or 'Unable to auto-resolve'}", 
                mapping=mapping
            )
    
    async def _handle_deletions(
        self,
        google_deleted_ids: set[str],
        icloud_deleted_ids: set[str],
        mappings: List[EventMappingDB],
        google_calendar_id: str,
        icloud_calendar_id: str,
        calendar_mapping: CalendarMappingDB,
        sync_session: SyncSessionDB,
        sync_report: SyncReport,
        dry_run: bool
    ) -> None:
        """Handle deleted events with proper sync token validation.
        
        CRITICAL: Only process deletions when using sync tokens to avoid false positives
        from time window limitations.
        
        Args:
            google_events: Current Google events
            icloud_events: Current iCloud events
            mappings: Existing event mappings
            google_calendar_id: Google calendar ID
            icloud_calendar_id: iCloud calendar ID
            calendar_mapping: Calendar mapping with sync token info
            sync_session: Sync session
            sync_report: Sync report
            dry_run: Whether this is a dry run
        """
        # CRITICAL: Only process deletions if we have sync tokens
        # Time windows miss deletes and can cause false positives
        has_google_sync_token = bool(calendar_mapping.google_sync_token)
        has_icloud_sync_token = bool(calendar_mapping.icloud_sync_token)
        
        self.logger.info("🗑️  DELETION DETECTION CHECK:")
        self.logger.info(f"  📅 Calendar Mapping ID: {calendar_mapping.id}")
        self.logger.info(f"  🔑 Google sync token available: {'✅' if has_google_sync_token else '❌'}")
        self.logger.info(f"  🔑 iCloud sync token available: {'✅' if has_icloud_sync_token else '❌'}")
        self.logger.info(f"  🗑️  Google deletion candidates: {len(google_deleted_ids)}")
        self.logger.info(f"  🗑️  iCloud deletion candidates: {len(icloud_deleted_ids)}")
        
        if not has_google_sync_token and not has_icloud_sync_token:
            self.logger.warning("❌ DELETION DETECTION DISABLED")
            self.logger.warning("  🚫 No sync tokens available for reliable deletion detection")
            self.logger.warning("  ⚠️  Time window sync cannot detect deletions safely")
            return
        
        self.logger.info("✅ DELETION DETECTION ENABLED")
        if has_google_sync_token and has_icloud_sync_token:
            self.logger.info("  🎯 Full bidirectional deletion detection active")
        elif has_google_sync_token:
            self.logger.info("  📱 Google→iCloud deletion detection active (iCloud→Google limited)")
        else:
            self.logger.info("  🍎 iCloud→Google deletion detection active (Google→iCloud limited)")
        
        # Log counts for audit
        if google_deleted_ids:
            self.logger.info(f"  🗑️  Google deleted IDs: {list(google_deleted_ids)[:5]}{'...' if len(google_deleted_ids) > 5 else ''}")
        if icloud_deleted_ids:
            self.logger.info(f"  🗑️  iCloud deleted IDs: {list(icloud_deleted_ids)[:5]}{'...' if len(icloud_deleted_ids) > 5 else ''}")
        
        self.logger.info(f"📊 Processing {len(mappings)} event mappings for deletion check")

        for mapping in mappings:
            # Only check active mappings
            if hasattr(mapping, 'sync_status') and mapping.sync_status != 'active':
                continue

            google_deleted = (
                has_google_sync_token and mapping.google_event_id in google_deleted_ids
            )
            icloud_deleted = (
                has_icloud_sync_token and mapping.icloud_event_id in icloud_deleted_ids
            )
            
            if google_deleted and not icloud_deleted:
                # Google event deleted, delete from iCloud
                if mapping.icloud_event_id and not dry_run:
                    try:
                        await self.icloud_service.delete_event(
                            icloud_calendar_id, mapping.icloud_event_id
                        )
                        await self._record_sync_operation(
                            sync_session, sync_report, SyncOperation.DELETE,
                            EventSource.GOOGLE, EventSource.ICLOUD, mapping.google_event_id,
                            "Deleted event", True, mapping=mapping
                        )
                    except Exception as e:
                        await self._record_sync_operation(
                            sync_session, sync_report, SyncOperation.DELETE,
                            EventSource.GOOGLE, EventSource.ICLOUD, mapping.google_event_id,
                            "Deleted event", False, error=str(e), mapping=mapping
                        )
            
            elif icloud_deleted and not google_deleted:
                # iCloud event deleted, delete from Google
                if mapping.google_event_id and not dry_run:
                    try:
                        await self.google_service.delete_event(
                            google_calendar_id, mapping.google_event_id
                        )
                        await self._record_sync_operation(
                            sync_session, sync_report, SyncOperation.DELETE,
                            EventSource.ICLOUD, EventSource.GOOGLE, mapping.icloud_event_id,
                            "Deleted event", True, mapping=mapping
                        )
                    except Exception as e:
                        await self._record_sync_operation(
                            sync_session, sync_report, SyncOperation.DELETE,
                            EventSource.ICLOUD, EventSource.GOOGLE, mapping.icloud_event_id,
                            "Deleted event", False, error=str(e), mapping=mapping
                        )
    
    async def _record_sync_operation(
        self,
        sync_session: SyncSessionDB,
        sync_report: SyncReport,
        operation: SyncOperation,
        source: EventSource,
        target: EventSource,
        event_id: str,
        event_summary: str,
        success: bool,
        error: Optional[str] = None,
        mapping: Optional[EventMappingDB] = None
    ) -> None:
        """Record sync operation in database and report.
        
        Args:
            sync_session: Sync session
            sync_report: Sync report
            operation: Operation type
            source: Source service
            target: Target service
            event_id: Event ID
            event_summary: Event summary
            success: Whether operation succeeded
            error: Error message if failed
            mapping: Event mapping if exists
        """
        # Update sync session counters
        direction = f"{source.value}_to_{target.value}"
        counter_key = f"{direction}_{operation.value}"
        
        if hasattr(sync_report, counter_key):
            current_value = getattr(sync_report, counter_key)
            setattr(sync_report, counter_key, current_value + 1)
        
        # Create sync result
        result = SyncResult(
            operation=operation,
            event_id=event_id,
            source=source,
            target=target,
            success=success,
            error_message=error,
            event_summary=event_summary
        )
        sync_report.results.append(result)
        
        # Record in database
        with self.db_manager.get_session() as session:
            self.db_manager.create_sync_operation(
                session, sync_session,
                operation.value, source.value, target.value,
                event_id, event_summary, success, error, mapping
            )
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status and statistics.
        
        Returns:
            Dictionary with sync status information
        """
        with self.db_manager.get_session() as session:
            recent_sessions = self.db_manager.get_recent_sync_sessions(session, limit=5)
            unresolved_conflicts = self.db_manager.get_unresolved_conflicts(session)
            
            # Get total event mappings
            total_mappings = session.query(EventMappingDB).count()
            
            status = {
                'total_event_mappings': total_mappings,
                'unresolved_conflicts': len(unresolved_conflicts),
                'recent_sessions': []
            }
            
            for sess in recent_sessions:
                session_info = {
                    'id': str(sess.id),
                    'started_at': sess.started_at.isoformat(),
                    'completed_at': sess.completed_at.isoformat() if sess.completed_at else None,
                    'status': sess.status,
                    'dry_run': sess.dry_run,
                    'operations': {
                        'google_to_icloud': {
                            'created': sess.google_to_icloud_created,
                            'updated': sess.google_to_icloud_updated,
                            'deleted': sess.google_to_icloud_deleted,
                            'skipped': sess.google_to_icloud_skipped
                        },
                        'icloud_to_google': {
                            'created': sess.icloud_to_google_created,
                            'updated': sess.icloud_to_google_updated,
                            'deleted': sess.icloud_to_google_deleted,
                            'skipped': sess.icloud_to_google_skipped
                        }
                    }
                }
                status['recent_sessions'].append(session_info)
            
            return status
    
    def _group_recurrence_events(self, events: Dict[str, CalendarEvent]) -> Dict[str, Dict[str, Any]]:
        """Group recurrence override events with their master events.
        
        Args:
            events: Dictionary of events by ID
            
        Returns:
            Dictionary of grouped events with master and overrides
        """
        grouped = {}
        master_events = {}
        override_events = []
        
        # First pass: identify master events and overrides
        for event in events.values():
            if self._is_recurrence_override(event):
                override_events.append(event)
            else:
                # This is a master event or standalone event
                grouped[event.id] = {
                    'master': event,
                    'overrides': []
                }
                if event.uid:
                    master_events[event.uid] = event.id
        
        # Second pass: link overrides to their masters
        for override_event in override_events:
            master_id = self._find_master_event_id(override_event, grouped, master_events)
            
            if master_id and master_id in grouped:
                grouped[master_id]['overrides'].append(override_event)
            else:
                # Orphaned override - treat as standalone event
                self.logger.warning(f"Orphaned recurrence override event: {override_event.id}")
                grouped[override_event.id] = {
                    'master': override_event,
                    'overrides': []
                }
        
        return grouped
    
    def _is_recurrence_override(self, event: CalendarEvent) -> bool:
        """Check if an event is a recurrence override.
        
        Args:
            event: Calendar event to check
            
        Returns:
            True if event is a recurrence override
        """
        # Check recurrence_overrides field
        if event.recurrence_overrides:
            for override in event.recurrence_overrides:
                if override.get('type') == 'recurrence-id' and override.get('is_override'):
                    return True
        
        # Google Calendar specific: recurringEventId indicates override
        if hasattr(event, 'recurring_event_id') and event.recurring_event_id:
            return True
            
        return False
    
    def _find_master_event_id(
        self, 
        override_event: CalendarEvent, 
        grouped: Dict[str, Dict[str, Any]], 
        master_events: Dict[str, str]
    ) -> Optional[str]:
        """Find the master event ID for a recurrence override.
        
        Args:
            override_event: The override event
            grouped: Current grouped events
            master_events: Mapping of UIDs to event IDs
            
        Returns:
            Master event ID if found
        """
        # Try to find master by Google's recurringEventId
        if hasattr(override_event, 'recurring_event_id') and override_event.recurring_event_id:
            if override_event.recurring_event_id in grouped:
                return override_event.recurring_event_id
        
        # Try to find master by UID (iCloud/iCal standard)
        if override_event.uid and override_event.uid in master_events:
            return master_events[override_event.uid]
        
        return None
    
    async def _fetch_google_change_set_with_retry(
        self,
        calendar_id: str,
        sync_token: Optional[str],
        time_min: datetime,
        time_max: datetime,
        last_sync_time: Optional[datetime],
        calendar_mapping: CalendarMappingDB
    ) -> ChangeSet[CalendarEvent]:
        """Fetch Google change set with proper token invalidation handling.
        
        Args:
            calendar_id: Google calendar ID
            sync_token: Current sync token
            time_min: Minimum time for events
            time_max: Maximum time for events
            last_sync_time: Last sync time
            calendar_mapping: Calendar mapping for token updates
            
        Returns:
            Change set with events
            
        Raises:
            CalendarServiceError: If fetching fails after retry
        """
        try:
            return await self.google_service.get_change_set(
                calendar_id,
                time_min=None if sync_token else time_min,
                time_max=None if sync_token else time_max,
                max_results=self.settings.sync_config.max_events_per_sync,
                updated_min=None if sync_token else last_sync_time,
                sync_token=sync_token
            )
        except GoogleCalendarService.TokenInvalid as e:
            return await self._handle_google_token_invalidation(
                calendar_id, time_min, time_max, calendar_mapping, e
            )
        except Exception as e:
            self.logger.error(f"Failed to fetch Google change set: {e}")
            raise CalendarServiceError(f"Failed to fetch Google events from {calendar_id}: {e}")
    
    async def _handle_google_token_invalidation(
        self,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
        calendar_mapping: CalendarMappingDB,
        original_error: Exception
    ) -> ChangeSet[CalendarEvent]:
        """Handle Google sync token invalidation with proper recovery.
        
        Args:
            calendar_id: Google calendar ID
            time_min: Minimum time for events
            time_max: Maximum time for events
            calendar_mapping: Calendar mapping to update
            original_error: The original TokenInvalid error
            
        Returns:
            Change set from fallback sync
        """
        self.logger.warning(
            f"Google sync token invalid for calendar {calendar_id}: {original_error}. "
            "Clearing token and performing full resync."
        )
        
        # Clear invalid token and timestamp
        try:
            with self.db_manager.get_session() as session:
                # Refresh the mapping object in this session
                mapping = session.merge(calendar_mapping)
                mapping.google_sync_token = None
                mapping.google_last_updated = None
                session.commit()
                # Update the in-memory object too
                calendar_mapping.google_sync_token = None
                calendar_mapping.google_last_updated = None
        except Exception as db_error:
            self.logger.error(f"Failed to clear invalid Google sync token: {db_error}")
            # Continue with sync anyway
        
        # Perform fallback sync without token
        try:
            g_cs = await self.google_service.get_change_set(
                calendar_id,
                time_min=time_min,
                time_max=time_max,
                max_results=self.settings.sync_config.max_events_per_sync,
                updated_min=None,
                sync_token=None
            )
            # Clear deletions during token recovery to avoid false positives
            g_cs.deleted_native_ids = set()
            return g_cs
        except Exception as fallback_error:
            self.logger.error(f"Fallback Google sync also failed: {fallback_error}")
            raise CalendarServiceError(
                f"Both sync token and fallback sync failed for Google calendar {calendar_id}"
            )
    
    async def _map_icloud_hrefs_to_event_ids(
        self,
        deleted_hrefs: set[str],
        mappings: List[EventMappingDB],
        calendar_id: str
    ) -> set[str]:
        """Map iCloud resource HREFs to event IDs with improved logic.
        
        Args:
            deleted_hrefs: Set of deleted resource HREFs
            mappings: Existing event mappings
            calendar_id: iCloud calendar ID
            
        Returns:
            Set of mapped event IDs
        """
        if not deleted_hrefs:
            return set()
        
        mapped_ids: set[str] = set()
        href_to_id: Dict[str, str] = {}
        normalized_mappings: Dict[str, str] = {}
        
        # Build mapping dictionaries
        for mapping in mappings:
            if mapping.icloud_resource_url and mapping.icloud_event_id:
                # Direct mapping
                href_to_id[mapping.icloud_resource_url] = mapping.icloud_event_id
                
                # Normalized mapping (extract filename for fuzzy matching)
                normalized_url = self._normalize_resource_url(mapping.icloud_resource_url)
                if normalized_url:
                    normalized_mappings[normalized_url] = mapping.icloud_event_id
        
        matched_count = 0
        unmatched_hrefs = []
        
        for href in deleted_hrefs:
            mapped_id = None
            
            # 1. Exact match
            if href in href_to_id:
                mapped_id = href_to_id[href]
                self.logger.debug(f"Exact match for href: {href}")
            
            # 2. Suffix matching (for relative vs absolute URLs)
            elif not mapped_id:
                for resource_url, event_id in href_to_id.items():
                    if self._urls_match(href, resource_url):
                        mapped_id = event_id
                        self.logger.debug(f"Suffix match for href {href} -> {resource_url}")
                        break
            
            # 3. Normalized/filename matching (last resort)
            elif not mapped_id:
                normalized_href = self._normalize_resource_url(href)
                if normalized_href and normalized_href in normalized_mappings:
                    mapped_id = normalized_mappings[normalized_href]
                    self.logger.debug(f"Normalized match for href {href} -> {normalized_href}")
            
            if mapped_id:
                mapped_ids.add(mapped_id)
                matched_count += 1
            else:
                unmatched_hrefs.append(href)
        
        # Log mapping results
        if matched_count > 0:
            self.logger.info(f"Mapped {matched_count}/{len(deleted_hrefs)} iCloud deletion HREFs to event IDs")
        
        if unmatched_hrefs:
            self.logger.warning(
                f"Could not map {len(unmatched_hrefs)} iCloud deletion HREFs: "
                f"{unmatched_hrefs[:3]}{'...' if len(unmatched_hrefs) > 3 else ''}"
            )
            # Store unmapped HREFs for manual investigation if needed
            self._log_unmapped_hrefs(unmatched_hrefs, calendar_id)
        
        return mapped_ids
    
    def _normalize_resource_url(self, url: str) -> Optional[str]:
        """Extract the resource identifier from a URL.
        
        Args:
            url: Resource URL
            
        Returns:
            Normalized identifier or None
        """
        if not url:
            return None
        
        # Extract the last path component (usually the UID + .ics)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path_parts = parsed.path.strip('/').split('/')
            if path_parts:
                # Get the filename (e.g., "event-uid.ics")
                filename = path_parts[-1]
                # Remove .ics extension if present
                if filename.endswith('.ics'):
                    filename = filename[:-4]
                return filename.lower()
        except Exception as e:
            self.logger.debug(f"Error normalizing URL {url}: {e}")
        
        return None
    
    def _urls_match(self, href1: str, href2: str) -> bool:
        """Check if two URLs refer to the same resource.
        
        Args:
            href1: First URL
            href2: Second URL
            
        Returns:
            True if URLs likely refer to the same resource
        """
        if not href1 or not href2:
            return False
        
        # Exact match
        if href1 == href2:
            return True
        
        # One is suffix of the other (relative vs absolute)
        if href1.endswith(href2) or href2.endswith(href1):
            return True
        
        # Both end with the same path component
        try:
            from urllib.parse import urlparse
            path1 = urlparse(href1).path.strip('/')
            path2 = urlparse(href2).path.strip('/')
            
            if path1 and path2:
                # Compare the last 2-3 path components
                parts1 = path1.split('/')[-3:]
                parts2 = path2.split('/')[-3:]
                
                # If the last few components match, likely the same resource
                if len(parts1) >= 2 and len(parts2) >= 2:
                    return parts1[-2:] == parts2[-2:]
                    
        except Exception:
            pass
        
        return False
    
    def _log_unmapped_hrefs(self, unmapped_hrefs: List[str], calendar_id: str) -> None:
        """Log unmapped HREFs for troubleshooting.
        
        Args:
            unmapped_hrefs: List of HREFs that couldn't be mapped
            calendar_id: Calendar ID for context
        """
        # Only log a few examples to avoid spam
        examples = unmapped_hrefs[:5]
        self.logger.info(
            f"Unmapped iCloud HREFs for calendar {calendar_id[:20]}...: {examples}"
        )
        
        # Log pattern analysis for troubleshooting
        if examples:
            patterns = set()
            for href in examples:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(href)
                    pattern = f"{parsed.netloc}{'/'.join(parsed.path.split('/')[:-1])}"
                    patterns.add(pattern)
                except Exception:
                    continue
            
            if patterns:
                self.logger.debug(f"HREF patterns: {list(patterns)[:3]}")
    
    def _ensure_timezone_aware(self, dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware for safe comparison.
        
        Args:
            dt: Datetime object that may be timezone-naive or timezone-aware
            
        Returns:
            Timezone-aware datetime (assumes UTC for naive datetimes)
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