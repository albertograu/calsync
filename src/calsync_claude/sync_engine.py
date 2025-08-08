"""Enhanced sync engine with conflict resolution and async operations."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, AsyncIterator
from contextlib import asynccontextmanager

import pytz
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import Settings
from .database import DatabaseManager, EventMappingDB, SyncSessionDB, CalendarMappingDB
from .models import (
    CalendarEvent, EventSource, ConflictResolution, SyncOperation, 
    SyncResult, SyncReport, SyncConfiguration
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
        """Resolve conflict between two events.
        
        Args:
            google_event: Google Calendar event
            icloud_event: iCloud Calendar event
            mapping: Event mapping from database
            
        Returns:
            Tuple of (winning_event, resolution_reason)
        """
        if self.strategy == ConflictResolution.MANUAL:
            return None, "Manual resolution required"
        
        # First try sequence-based resolution (preferred for iCal events)
        google_seq = google_event.sequence or 0
        icloud_seq = icloud_event.sequence or 0
        
        if google_seq != icloud_seq:
            if google_seq > icloud_seq:
                return google_event, f"Google event has higher sequence ({google_seq} > {icloud_seq})"
            else:
                return icloud_event, f"iCloud event has higher sequence ({icloud_seq} > {google_seq})"
        
        # Fallback to timestamp-based resolution
        elif self.strategy == ConflictResolution.LATEST_WINS:
            if google_event.updated > icloud_event.updated:
                return google_event, "Google event is more recent"
            else:
                return icloud_event, "iCloud event is more recent"
        
        elif self.strategy == ConflictResolution.GOOGLE_WINS:
            return google_event, "Google wins policy"
        
        elif self.strategy == ConflictResolution.ICLOUD_WINS:
            return icloud_event, "iCloud wins policy"
        
        else:
            return None, f"Unknown resolution strategy: {self.strategy}"


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
        
        # Get events from both calendars with incremental sync support
        google_events = {}
        icloud_events = {}
        google_events_by_uid = {}
        icloud_events_by_uid = {}
        
        # Use sync tokens for true incremental sync if available
        google_sync_token = calendar_mapping.google_sync_token
        icloud_sync_token = calendar_mapping.icloud_sync_token
        
        # Store callbacks to capture new sync tokens
        new_google_sync_token = None
        new_icloud_sync_token = None
        
        def capture_google_token(token):
            nonlocal new_google_sync_token
            new_google_sync_token = token
        
        # Set callback for Google sync token capture  
        self.google_service._current_sync_token_callback = capture_google_token
        
        async for event in self.google_service.get_events(
            google_calendar_id, 
            time_min=time_min if not google_sync_token else None,
            time_max=time_max if not google_sync_token else None,
            max_results=self.settings.sync_config.max_events_per_sync,
            sync_token=google_sync_token  # Use sync token for incremental sync
        ):
            google_events[event.id] = event
            if event.uid:
                google_events_by_uid[event.uid] = event
        
        async for event in self.icloud_service.get_events(
            icloud_calendar_id, 
            time_min=time_min if not icloud_sync_token else None,
            time_max=time_max if not icloud_sync_token else None,
            max_results=self.settings.sync_config.max_events_per_sync,
            sync_token=icloud_sync_token  # Use sync token for incremental sync (TODO: implement)
        ):
            icloud_events[event.id] = event
            if event.uid:
                icloud_events_by_uid[event.uid] = event
        
        # Update sync tokens in database for next incremental sync
        if new_google_sync_token or new_icloud_sync_token:
            with self.db_manager.get_session() as session:
                if new_google_sync_token:
                    calendar_mapping.google_sync_token = new_google_sync_token
                    calendar_mapping.google_last_updated = datetime.now(pytz.UTC)
                if new_icloud_sync_token:
                    calendar_mapping.icloud_sync_token = new_icloud_sync_token 
                    calendar_mapping.icloud_last_updated = datetime.now(pytz.UTC)
                session.commit()
        
        self.logger.info(
            f"Found {len(google_events)} Google events, {len(icloud_events)} iCloud events"
        )
        
        # Get existing event mappings for this calendar pair
        with self.db_manager.get_session() as session:
            existing_mappings = session.query(EventMappingDB).filter(
                EventMappingDB.calendar_mapping_id == calendar_mapping.id
            ).all()
        
        mappings_by_google = {m.google_event_id: m for m in existing_mappings if m.google_event_id}
        mappings_by_icloud = {m.icloud_event_id: m for m in existing_mappings if m.icloud_event_id}
        
        # Track processed events
        processed_google = set()
        processed_icloud = set()
        
        # Check sync direction and perform appropriate syncs with UID-based deduplication
        if calendar_mapping.bidirectional or calendar_mapping.sync_direction == 'google_to_icloud':
            # Process Google -> iCloud sync
            for google_event in google_events.values():
                if google_event.id in processed_google:
                    continue
                
                # Check if event should be synced (UID-based deduplication)
                if google_event.should_sync_to_calendar(icloud_calendar_id, icloud_events):
                    await self._sync_event_to_target(
                        google_event, EventSource.ICLOUD, icloud_calendar_id,
                        calendar_mapping, mappings_by_google, sync_session, sync_report, dry_run,
                        target_events_by_uid=icloud_events_by_uid
                    )
                processed_google.add(google_event.id)
        
        if calendar_mapping.bidirectional or calendar_mapping.sync_direction == 'icloud_to_google':
            # Process iCloud -> Google sync
            for icloud_event in icloud_events.values():
                if icloud_event.id in processed_icloud:
                    continue
                
                # Check if event should be synced (UID-based deduplication)
                if icloud_event.should_sync_to_calendar(google_calendar_id, google_events):
                    await self._sync_event_to_target(
                        icloud_event, EventSource.GOOGLE, google_calendar_id,
                        calendar_mapping, mappings_by_icloud, sync_session, sync_report, dry_run,
                        target_events_by_uid=google_events_by_uid
                    )
                processed_icloud.add(icloud_event.id)
        
        # Handle deletions (events that exist in mapping but not in source)
        await self._handle_deletions(
            google_events, icloud_events, existing_mappings,
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
                    
                    # Create mapping with all necessary fields for production
                    with self.db_manager.get_session() as session:
                        if source_event.source == EventSource.GOOGLE:
                            # Extract resource info from created iCloud event
                            icloud_resource_url = None
                            if hasattr(created_event, 'original_data'):
                                icloud_resource_url = created_event.original_data.get('resource_url')
                            
                            mapping = EventMappingDB(
                                calendar_mapping_id=calendar_mapping.id,
                                google_event_id=source_event.id,
                                icloud_event_id=created_event.id,
                                google_calendar_id=calendar_mapping.google_calendar_id,
                                icloud_calendar_id=calendar_mapping.icloud_calendar_id,
                                
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
                                calendar_mapping_id=calendar_mapping.id,
                                google_event_id=created_event.id,
                                icloud_event_id=source_event.id,
                                google_calendar_id=calendar_mapping.google_calendar_id,
                                icloud_calendar_id=calendar_mapping.icloud_calendar_id,
                                
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
            self.logger.error(f"Failed to sync event {source_event.id}: {e}")
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
            source_modified = source_event.updated > mapping.last_sync_at
            target_modified = target_event.updated > mapping.last_sync_at
            
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
        """Handle conflict between events.
        
        Args:
            source_event: Source event
            target_event: Target event
            mapping: Event mapping
            target_calendar_id: Target calendar ID
            sync_session: Sync session
            sync_report: Sync report
            dry_run: Whether this is a dry run
        """
        self.logger.warning(f"Conflict detected for events {source_event.id} <-> {target_event.id}")
        
        # Try to resolve conflict
        winning_event, reason = self.conflict_resolver.resolve_conflict(
            source_event if source_event.source == EventSource.GOOGLE else target_event,
            target_event if target_event.source == EventSource.ICLOUD else source_event,
            mapping
        )
        
        if winning_event:
            # Apply resolution
            target_service = (
                self.icloud_service if target_event.source == EventSource.ICLOUD
                else self.google_service
            )
            
            if not dry_run:
                await target_service.update_event(
                    target_calendar_id, target_event.id, winning_event
                )
                
                # Update mapping
                with self.db_manager.get_session() as session:
                    self.db_manager.update_event_mapping(
                        session, mapping,
                        content_hash=winning_event.content_hash(),
                        sync_direction=f"{winning_event.source.value}_wins"
                    )
            
            self.logger.info(f"Conflict resolved: {reason}")
            
            await self._record_sync_operation(
                sync_session, sync_report, SyncOperation.UPDATE,
                source_event.source, target_event.source, source_event.id,
                source_event.summary, True, mapping=mapping
            )
        else:
            # Manual resolution required
            with self.db_manager.get_session() as session:
                self.db_manager.create_conflict(
                    session, sync_session, "content_mismatch",
                    google_event_id=source_event.id if source_event.source == EventSource.GOOGLE else target_event.id,
                    icloud_event_id=target_event.id if target_event.source == EventSource.ICLOUD else source_event.id,
                    google_event_data=json.dumps(source_event.dict() if source_event.source == EventSource.GOOGLE else target_event.dict()),
                    icloud_event_data=json.dumps(target_event.dict() if target_event.source == EventSource.ICLOUD else source_event.dict())
                )
            
            sync_report.conflicts.append({
                'source_event_id': source_event.id,
                'target_event_id': target_event.id,
                'reason': reason,
                'resolution': 'manual_required'
            })
    
    async def _handle_deletions(
        self,
        google_events: Dict[str, CalendarEvent],
        icloud_events: Dict[str, CalendarEvent],
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
        
        if not has_google_sync_token and not has_icloud_sync_token:
            self.logger.warning(
                "Skipping deletion detection - no sync tokens available. "
                "Time window sync cannot reliably detect deletions."
            )
            return
        
        for mapping in mappings:
            # Only check active mappings
            if hasattr(mapping, 'sync_status') and mapping.sync_status != 'active':
                continue
                
            google_deleted = (
                has_google_sync_token and 
                mapping.google_event_id and 
                mapping.google_event_id not in google_events
            )
            icloud_deleted = (
                has_icloud_sync_token and 
                mapping.icloud_event_id and 
                mapping.icloud_event_id not in icloud_events
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