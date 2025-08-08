"""Calendar management and mapping logic."""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass

from .config import Settings
from .database import DatabaseManager, CalendarMappingDB
from .models import CalendarInfo, CalendarMapping, CalendarPair, EventSource
from .services import GoogleCalendarService, iCloudCalendarService

logger = logging.getLogger(__name__)


@dataclass
class CalendarMatchResult:
    """Result of calendar matching operation."""
    matched_pairs: List[Tuple[CalendarInfo, CalendarInfo]]
    unmatched_google: List[CalendarInfo]
    unmatched_icloud: List[CalendarInfo]
    existing_mappings: List[CalendarMapping]


class CalendarManager:
    """Manages calendar discovery, mapping, and synchronization setup."""
    
    def __init__(
        self,
        settings: Settings,
        google_service: GoogleCalendarService,
        icloud_service: iCloudCalendarService,
        db_manager: DatabaseManager
    ):
        """Initialize calendar manager.
        
        Args:
            settings: Application settings
            google_service: Google Calendar service
            icloud_service: iCloud Calendar service
            db_manager: Database manager
        """
        self.settings = settings
        self.google_service = google_service
        self.icloud_service = icloud_service
        self.db_manager = db_manager
        self.logger = logger.getChild('calendar_manager')
    
    async def discover_calendars(self) -> Tuple[List[CalendarInfo], List[CalendarInfo]]:
        """Discover all available calendars from both services.
        
        Returns:
            Tuple of (google_calendars, icloud_calendars)
        """
        try:
            google_calendars, icloud_calendars = await asyncio.gather(
                self.google_service.get_calendars(),
                self.icloud_service.get_calendars()
            )
            
            self.logger.info(
                f"Discovered {len(google_calendars)} Google calendars, "
                f"{len(icloud_calendars)} iCloud calendars"
            )
            
            return google_calendars, icloud_calendars
            
        except Exception as e:
            self.logger.error(f"Failed to discover calendars: {e}")
            raise
    
    def get_configured_mappings(self) -> List[CalendarMapping]:
        """Get calendar mappings from configuration.
        
        NOTE: This method is DEPRECATED and only supports legacy configurations.
        New configurations should use explicit calendar_pairs instead.
        
        Returns:
            List of calendar mappings from config
        """
        mappings = []
        
        # Priority 1: Use explicit calendar pairs (new format)
        if self.settings.sync_config.has_explicit_pairs():
            self.logger.info("Using explicit calendar pairs from configuration")
            # Convert CalendarPair to CalendarMapping for backward compatibility
            for pair in self.settings.sync_config.get_active_pairs():
                mapping = CalendarMapping(
                    google_calendar_id=pair.google_calendar_id,
                    icloud_calendar_id=pair.icloud_calendar_id,
                    google_calendar_name=pair.google_calendar_name,
                    icloud_calendar_name=pair.icloud_calendar_name,
                    bidirectional=pair.bidirectional,
                    sync_direction=pair.sync_direction,
                    enabled=pair.enabled,
                    conflict_resolution=pair.conflict_resolution
                )
                mappings.append(mapping)
            return mappings
        
        # Priority 2: Convert legacy calendar_mappings
        if self.settings.sync_config.calendar_mappings:
            self.logger.warning("Using legacy calendar_mappings - consider upgrading to calendar_pairs")
            mappings.extend(self.settings.sync_config.calendar_mappings)
            return mappings
        
        # Priority 3: Legacy selected_* lists (DEPRECATED - cross product eliminated!)
        if (self.settings.sync_config.selected_google_calendars or 
            self.settings.sync_config.selected_icloud_calendars):
            
            self.logger.warning(
                "DEPRECATED: selected_google_calendars/selected_icloud_calendars are deprecated. "
                "Cross-product sync has been eliminated. Only 1:1 pairing is supported. "
                "Please migrate to explicit calendar_pairs configuration."
            )
            
            google_cals = self.settings.sync_config.selected_google_calendars or ["primary"]
            icloud_cals = self.settings.sync_config.selected_icloud_calendars or []
            
            # ONLY create 1:1 mappings - NO cross product!
            if len(google_cals) == len(icloud_cals):
                self.logger.info(f"Creating {len(google_cals)} 1:1 calendar pairs from legacy config")
                for g_cal, i_cal in zip(google_cals, icloud_cals):
                    mappings.append(CalendarMapping(
                        google_calendar_id=g_cal,
                        icloud_calendar_id=i_cal
                    ))
            else:
                raise ValueError(
                    f"Legacy configuration error: Cannot create cross-product mappings. "
                    f"Found {len(google_cals)} Google calendars and {len(icloud_cals)} iCloud calendars. "
                    f"Please configure explicit calendar_pairs with 1:1 relationships."
                )
        
        return mappings
    
    def create_calendar_pair(
        self,
        google_calendar_id: str,
        icloud_calendar_id: str,
        name: Optional[str] = None,
        bidirectional: bool = True,
        sync_direction: Optional[str] = None,
        enabled: bool = True
    ) -> CalendarPair:
        """Create a new calendar pair configuration.
        
        Args:
            google_calendar_id: Google calendar ID
            icloud_calendar_id: iCloud calendar ID  
            name: Human-readable name for the pair
            bidirectional: Whether sync is bidirectional
            sync_direction: Sync direction if not bidirectional
            enabled: Whether the pair is enabled
            
        Returns:
            New CalendarPair instance
        """
        return CalendarPair(
            name=name,
            google_calendar_id=google_calendar_id,
            icloud_calendar_id=icloud_calendar_id,
            bidirectional=bidirectional,
            sync_direction=sync_direction,
            enabled=enabled
        )
    
    def validate_pairs_configuration(self, pairs: List[CalendarPair]) -> List[str]:
        """Validate calendar pairs configuration.
        
        Args:
            pairs: List of calendar pairs to validate
            
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        google_ids = []
        icloud_ids = []
        
        for i, pair in enumerate(pairs):
            if not pair.enabled:
                continue
                
            # Check for duplicate Google calendar IDs
            if pair.google_calendar_id in google_ids:
                errors.append(
                    f"Pair {i+1}: Google calendar '{pair.google_calendar_id}' "
                    "is already used in another pair"
                )
            else:
                google_ids.append(pair.google_calendar_id)
            
            # Check for duplicate iCloud calendar IDs
            if pair.icloud_calendar_id in icloud_ids:
                errors.append(
                    f"Pair {i+1}: iCloud calendar '{pair.icloud_calendar_id}' "
                    "is already used in another pair"
                )
            else:
                icloud_ids.append(pair.icloud_calendar_id)
            
            # Validate sync direction
            if not pair.bidirectional and not pair.sync_direction:
                errors.append(
                    f"Pair {i+1}: sync_direction must be specified when bidirectional=False"
                )
            elif not pair.bidirectional and pair.sync_direction not in ['google_to_icloud', 'icloud_to_google']:
                errors.append(
                    f"Pair {i+1}: invalid sync_direction '{pair.sync_direction}'"
                )
        
        return errors
    
    async def auto_match_calendars(
        self,
        google_calendars: List[CalendarInfo],
        icloud_calendars: List[CalendarInfo],
        existing_mappings: Optional[List[CalendarMapping]] = None
    ) -> CalendarMatchResult:
        """Automatically match calendars based on names and patterns.
        
        Args:
            google_calendars: Available Google calendars
            icloud_calendars: Available iCloud calendars  
            existing_mappings: Existing calendar mappings
            
        Returns:
            Calendar match result
        """
        if existing_mappings is None:
            existing_mappings = self.get_configured_mappings()
        
        matched_pairs = []
        used_google = set()
        used_icloud = set()
        
        # First, handle explicit configured mappings
        for mapping in existing_mappings:
            google_cal = self._find_google_calendar(
                google_calendars, mapping.google_calendar_id
            )
            icloud_cal = self._find_icloud_calendar(
                icloud_calendars, mapping.icloud_calendar_id
            )
            
            if google_cal and icloud_cal:
                matched_pairs.append((google_cal, icloud_cal))
                used_google.add(google_cal.id)
                used_icloud.add(icloud_cal.id)
                self.logger.info(
                    f"Configured mapping: '{google_cal.name}' <-> '{icloud_cal.name}'"
                )
        
        # Auto-match remaining calendars by name similarity
        remaining_google = [c for c in google_calendars if c.id not in used_google]
        remaining_icloud = [c for c in icloud_calendars if c.id not in used_icloud]
        
        for google_cal in remaining_google:
            best_match = self._find_best_name_match(google_cal.name, remaining_icloud)
            if best_match and best_match.id not in used_icloud:
                matched_pairs.append((google_cal, best_match))
                used_google.add(google_cal.id)
                used_icloud.add(best_match.id)
                remaining_icloud.remove(best_match)
                self.logger.info(
                    f"Auto-matched: '{google_cal.name}' <-> '{best_match.name}'"
                )
        
        # Handle special case: map remaining iCloud calendars to Google primary
        if remaining_icloud and self.settings.sync_config.auto_create_calendars:
            primary_google = next((c for c in google_calendars if c.is_primary), None)
            if primary_google and primary_google.id not in used_google:
                for icloud_cal in remaining_icloud[:]:  # Copy list to modify during iteration
                    matched_pairs.append((primary_google, icloud_cal))
                    used_icloud.add(icloud_cal.id)
                    remaining_icloud.remove(icloud_cal)
                    self.logger.info(
                        f"Mapped to primary: '{primary_google.name}' <-> '{icloud_cal.name}'"
                    )
        
        unmatched_google = [c for c in google_calendars if c.id not in used_google]
        unmatched_icloud = [c for c in icloud_calendars if c.id not in used_icloud]
        
        return CalendarMatchResult(
            matched_pairs=matched_pairs,
            unmatched_google=unmatched_google,
            unmatched_icloud=unmatched_icloud,
            existing_mappings=existing_mappings
        )
    
    async def create_calendar_mappings(
        self,
        matched_pairs: List[Tuple[CalendarInfo, CalendarInfo]],
        bidirectional: bool = True,
        conflict_resolution: Optional[str] = None
    ) -> List[CalendarMappingDB]:
        """Create database mappings for matched calendar pairs.
        
        Args:
            matched_pairs: List of (Google, iCloud) calendar pairs
            bidirectional: Whether sync should be bidirectional
            conflict_resolution: Override conflict resolution
            
        Returns:
            List of created database mappings
        """
        mappings = []
        
        with self.db_manager.get_session() as session:
            for google_cal, icloud_cal in matched_pairs:
                # Check if mapping already exists
                existing = self.db_manager.get_calendar_mapping(
                    session, google_cal.id, icloud_cal.id
                )
                
                if existing:
                    self.logger.info(
                        f"Mapping already exists: {google_cal.name} <-> {icloud_cal.name}"
                    )
                    mappings.append(existing)
                    continue
                
                # Create new mapping
                mapping = self.db_manager.create_calendar_mapping(
                    session=session,
                    google_calendar_id=google_cal.id,
                    icloud_calendar_id=icloud_cal.id,
                    google_calendar_name=google_cal.name,
                    icloud_calendar_name=icloud_cal.name,
                    bidirectional=bidirectional,
                    conflict_resolution=conflict_resolution
                )
                mappings.append(mapping)
                
                self.logger.info(
                    f"Created mapping: {google_cal.name} <-> {icloud_cal.name}"
                )
        
        return mappings
    
    async def create_missing_calendars(
        self,
        unmatched_google: List[CalendarInfo],
        unmatched_icloud: List[CalendarInfo]
    ) -> List[Tuple[CalendarInfo, CalendarInfo]]:
        """Create missing calendars to enable full sync.
        
        Args:
            unmatched_google: Unmatched Google calendars
            unmatched_icloud: Unmatched iCloud calendars
            
        Returns:
            List of newly created calendar pairs
        """
        if not self.settings.sync_config.auto_create_calendars:
            self.logger.info("Auto-create calendars disabled, skipping creation")
            return []
        
        created_pairs = []
        
        # Create iCloud calendars for unmatched Google calendars
        for google_cal in unmatched_google:
            if google_cal.is_primary:
                continue  # Don't try to create primary calendar equivalent
            
            try:
                # Note: CalDAV calendar creation is complex and service-dependent
                # This would require additional implementation in iCloud service
                self.logger.info(f"Would create iCloud calendar for: {google_cal.name}")
                # Implementation would go here
                
            except Exception as e:
                self.logger.warning(
                    f"Failed to create iCloud calendar for {google_cal.name}: {e}"
                )
        
        # Create Google calendars for unmatched iCloud calendars  
        for icloud_cal in unmatched_icloud:
            try:
                # Create Google calendar with same name
                calendar_data = {
                    'summary': icloud_cal.name,
                    'description': f'Auto-created for iCloud calendar sync',
                    'timeZone': icloud_cal.timezone or 'UTC'
                }
                
                # Note: This requires additional method in Google service
                self.logger.info(f"Would create Google calendar for: {icloud_cal.name}")
                # Implementation would go here
                
            except Exception as e:
                self.logger.warning(
                    f"Failed to create Google calendar for {icloud_cal.name}: {e}"
                )
        
        return created_pairs
    
    def _find_google_calendar(
        self,
        calendars: List[CalendarInfo],
        identifier: str
    ) -> Optional[CalendarInfo]:
        """Find Google calendar by ID or name."""
        # Direct ID match
        for cal in calendars:
            if cal.id == identifier:
                return cal
        
        # Name match (case-insensitive)
        for cal in calendars:
            if cal.name.lower() == identifier.lower():
                return cal
        
        # Primary calendar special case
        if identifier == "primary":
            for cal in calendars:
                if cal.is_primary:
                    return cal
        
        return None
    
    def _find_icloud_calendar(
        self,
        calendars: List[CalendarInfo],
        identifier: str
    ) -> Optional[CalendarInfo]:
        """Find iCloud calendar by ID or name."""
        # Direct ID match
        for cal in calendars:
            if cal.id == identifier:
                return cal
        
        # Name match (case-insensitive)
        for cal in calendars:
            if cal.name.lower() == identifier.lower():
                return cal
        
        return None
    
    def _find_best_name_match(
        self,
        target_name: str,
        candidates: List[CalendarInfo],
        threshold: float = 0.8
    ) -> Optional[CalendarInfo]:
        """Find best name match using fuzzy matching."""
        if not candidates:
            return None
        
        # Simple exact match first
        for candidate in candidates:
            if candidate.name.lower() == target_name.lower():
                return candidate
        
        # Fuzzy matching could be implemented here using libraries like fuzzywuzzy
        # For now, we'll use simple substring matching
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            # Simple similarity: how much of the target is contained in candidate
            target_lower = target_name.lower()
            candidate_lower = candidate.name.lower()
            
            if target_lower in candidate_lower or candidate_lower in target_lower:
                # Calculate rough similarity score
                longer = max(len(target_lower), len(candidate_lower))
                shorter = min(len(target_lower), len(candidate_lower))
                score = shorter / longer
                
                if score > best_score and score >= threshold:
                    best_score = score
                    best_match = candidate
        
        return best_match
    
    async def get_all_mappings(self) -> List[CalendarMappingDB]:
        """Get all calendar mappings from database.
        
        Returns:
            List of all calendar mappings
        """
        with self.db_manager.get_session() as session:
            return self.db_manager.get_calendar_mappings(session)
    
    async def update_mapping(
        self,
        mapping_id: str,
        **kwargs
    ) -> Optional[CalendarMappingDB]:
        """Update a calendar mapping.
        
        Args:
            mapping_id: Mapping ID to update
            **kwargs: Fields to update
            
        Returns:
            Updated mapping or None if not found
        """
        with self.db_manager.get_session() as session:
            mapping = session.query(CalendarMappingDB).filter(
                CalendarMappingDB.id == mapping_id
            ).first()
            
            if not mapping:
                return None
            
            return self.db_manager.update_calendar_mapping(session, mapping, **kwargs)
    
    async def delete_mapping(self, mapping_id: str) -> bool:
        """Delete a calendar mapping.
        
        Args:
            mapping_id: Mapping ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        with self.db_manager.get_session() as session:
            mapping = session.query(CalendarMappingDB).filter(
                CalendarMappingDB.id == mapping_id
            ).first()
            
            if not mapping:
                return False
            
            self.db_manager.delete_calendar_mapping(session, mapping)
            return True