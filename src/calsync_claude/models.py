"""Data models for calendar synchronization."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, List, Set, TypeVar, Generic
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, validator
import pytz


class EventSource(str, Enum):
    """Event source enumeration."""
    
    GOOGLE = "google"
    ICLOUD = "icloud"


class ConflictResolution(str, Enum):
    """Conflict resolution strategies."""
    
    MANUAL = "manual"  # Mark conflicts for manual resolution
    LATEST_WINS = "latest_wins"  # Most recently modified event wins
    GOOGLE_WINS = "google_wins"  # Google Calendar event wins
    ICLOUD_WINS = "icloud_wins"  # iCloud Calendar event wins


class SyncOperation(str, Enum):
    """Sync operation types."""
    
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SKIP = "skip"


class CalendarEvent(BaseModel):
    """Standardized calendar event model."""
    
    id: str = Field(..., description="Event ID from source service")
    uid: Optional[str] = Field(None, description="Universal event UID (iCal UID for deduplication)")
    source: EventSource = Field(..., description="Source service")
    summary: str = Field("", description="Event title/summary")
    description: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Event location")
    start: datetime = Field(..., description="Event start time")
    end: datetime = Field(..., description="Event end time")
    all_day: bool = Field(False, description="Whether event is all-day")
    timezone: Optional[str] = Field(None, description="Original IANA timezone for non-all-day events")
    created: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    updated: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    etag: Optional[str] = Field(None, description="ETag for change detection")
    sequence: Optional[int] = Field(None, description="iCal SEQUENCE field for conflict resolution")
    recurring_event_id: Optional[str] = Field(None, description="Recurring event ID")
    recurrence_rule: Optional[str] = Field(None, description="RRULE for recurring events")
    recurrence_overrides: List[Dict[str, Any]] = Field(default_factory=list, description="Recurrence exceptions/modifications")
    organizer: Optional[Dict[str, Any]] = Field(None, description="Event organizer info")
    attendees: List[Dict[str, Any]] = Field(default_factory=list, description="Event attendees")
    original_data: Optional[Dict[str, Any]] = Field(None, description="Original event data")
    
    @validator('start', 'end', 'created', 'updated', pre=True)
    def ensure_timezone_aware(cls, v):
        """Ensure datetime objects are timezone-aware."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=pytz.UTC)
        return v
    
    @validator('end')
    def end_after_start(cls, v, values):
        """Ensure end time is after start time."""
        if 'start' in values and v <= values['start']:
            # More detailed error message for debugging
            start_time = values['start']
            raise ValueError(
                f'End time ({v}) must be after start time ({start_time}). '
                f'This usually indicates timezone conversion issues or corrupted event data.'
            )
        return v
    
    def content_hash(self) -> str:
        """Generate content hash for change detection."""
        import hashlib
        import json
        
        # Include all fields that should trigger a sync when changed
        content = {
            'uid': self.uid,
            'summary': self.summary,
            'description': self.description or '',
            'location': self.location or '',
            'start': self.start.isoformat(),
            'end': self.end.isoformat(),
            'all_day': self.all_day,
            'timezone': self.timezone,
            'recurrence_rule': self.recurrence_rule,
            # Include attendees and organizer to detect meeting changes
            'organizer': json.dumps(self.organizer, sort_keys=True) if self.organizer else None,
            'attendees': json.dumps(self.attendees, sort_keys=True) if self.attendees else [],
        }
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()
    
    def get_dedup_key(self) -> str:
        """Get key for deduplication based on UID or content hash."""
        return self.uid if self.uid else self.content_hash()
    
    def should_sync_to_calendar(self, target_calendar_id: str, existing_events: Dict[str, 'CalendarEvent']) -> bool:
        """Check if this event should be synced to target calendar."""
        dedup_key = self.get_dedup_key()
        
        # Check if an event with same UID already exists in target
        for existing_event in existing_events.values():
            if existing_event.get_dedup_key() == dedup_key:
                return False  # Don't sync, duplicate exists
        
        return True
    
    def is_recurrence_master(self) -> bool:
        """Check if this is a master recurring event."""
        return bool(self.recurrence_rule and not self.is_recurrence_override())
    
    def is_recurrence_override(self) -> bool:
        """Check if this is a recurrence override/exception event."""
        if self.recurrence_overrides:
            for override in self.recurrence_overrides:
                if override.get('type') == 'recurrence-id' and override.get('is_override'):
                    return True
        # Google Calendar specific check
        return bool(hasattr(self, 'recurring_event_id') and getattr(self, 'recurring_event_id'))
    
    def get_recurrence_id(self) -> Optional[str]:
        """Get the recurrence ID if this is an override event."""
        if self.recurrence_overrides:
            for override in self.recurrence_overrides:
                if override.get('type') == 'recurrence-id':
                    return override.get('recurrence_id')
        return None
    
    def get_master_event_id(self) -> Optional[str]:
        """Get the master event ID if this is an override."""
        if self.recurrence_overrides:
            for override in self.recurrence_overrides:
                if override.get('type') == 'recurrence-id':
                    return override.get('master_event_id')
        # Google Calendar specific
        if hasattr(self, 'recurring_event_id'):
            return getattr(self, 'recurring_event_id')
        return None
    
    def to_dict_for_comparison(self) -> Dict[str, Any]:
        """Convert to dictionary for comparison (excluding volatile fields)."""
        return {
            'uid': self.uid,
            'summary': self.summary,
            'description': self.description,
            'location': self.location,
            'start': self.start.isoformat(),
            'end': self.end.isoformat(),
            'all_day': self.all_day,
            'timezone': self.timezone,
            'sequence': self.sequence,
            'recurrence_rule': self.recurrence_rule,
            'recurrence_overrides': self.recurrence_overrides
        }


class EventMapping(BaseModel):
    """Event mapping between different calendar services."""
    
    id: UUID = Field(default_factory=uuid4)
    google_event_id: Optional[str] = Field(None)
    icloud_event_id: Optional[str] = Field(None)
    google_etag: Optional[str] = Field(None)
    icloud_etag: Optional[str] = Field(None)
    content_hash: str = Field(..., description="Hash of event content")
    created_at: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    last_sync_at: Optional[datetime] = Field(None)
    sync_direction: Optional[str] = Field(None, description="Last sync direction")


class SyncResult(BaseModel):
    """Result of a synchronization operation."""
    
    operation: SyncOperation
    event_id: str
    source: EventSource
    target: EventSource
    success: bool
    error_message: Optional[str] = None
    event_summary: Optional[str] = None
    conflict: bool = False


class SyncReport(BaseModel):
    """Comprehensive sync report."""
    
    sync_id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    completed_at: Optional[datetime] = Field(None)
    dry_run: bool = Field(False)
    
    # Operation counts
    google_to_icloud_created: int = Field(0)
    google_to_icloud_updated: int = Field(0)
    google_to_icloud_deleted: int = Field(0)
    google_to_icloud_skipped: int = Field(0)
    
    icloud_to_google_created: int = Field(0)
    icloud_to_google_updated: int = Field(0)
    icloud_to_google_deleted: int = Field(0)
    icloud_to_google_skipped: int = Field(0)
    
    # Results and errors
    results: List[SyncResult] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    
    @property
    def total_operations(self) -> int:
        """Total number of operations performed."""
        return len(self.results)
    
    @property
    def success_rate(self) -> float:
        """Success rate of operations."""
        if not self.results:
            return 1.0
        successful = sum(1 for r in self.results if r.success)
        return successful / len(self.results)


class CalendarInfo(BaseModel):
    """Calendar information model."""
    
    id: str = Field(..., description="Calendar ID")
    name: str = Field(..., description="Calendar name")
    source: EventSource = Field(..., description="Calendar source")
    description: Optional[str] = Field(None)
    timezone: str = Field("UTC")
    color: Optional[str] = Field(None)
    access_role: Optional[str] = Field(None)
    is_primary: bool = Field(False)
    is_selected: bool = Field(True, description="Whether to sync this calendar")


class CalendarPair(BaseModel):
    """Explicit one-to-one calendar pairing configuration."""
    
    name: Optional[str] = Field(None, description="Human-readable name for this pair")
    google_calendar_id: str = Field(..., description="Google calendar ID")
    icloud_calendar_id: str = Field(..., description="iCloud calendar ID")
    google_calendar_name: Optional[str] = Field(None, description="Google calendar name (auto-populated)")
    icloud_calendar_name: Optional[str] = Field(None, description="iCloud calendar name (auto-populated)")
    bidirectional: bool = Field(True, description="Whether sync is bidirectional")
    sync_direction: Optional[str] = Field(None, description="Sync direction: 'google_to_icloud' or 'icloud_to_google'")
    enabled: bool = Field(True, description="Whether this pair is enabled for sync")
    conflict_resolution: Optional[ConflictResolution] = Field(None, description="Override global conflict resolution for this pair")
    
    @validator('sync_direction')
    def validate_sync_direction(cls, v, values):
        """Validate sync direction."""
        if v is not None and not values.get('bidirectional', True):
            valid_directions = ['google_to_icloud', 'icloud_to_google']
            if v not in valid_directions:
                raise ValueError(f'sync_direction must be one of {valid_directions}')
        return v
    
    def __str__(self) -> str:
        """String representation of the pair."""
        name = self.name or f"Pair {self.google_calendar_id[:8]}→{self.icloud_calendar_id[:8]}"
        direction = "↔" if self.bidirectional else ("→" if self.sync_direction == "google_to_icloud" else "←")
        return f"{name} ({self.google_calendar_name or 'Google'} {direction} {self.icloud_calendar_name or 'iCloud'})"


class CalendarMapping(BaseModel):
    """DEPRECATED: Use CalendarPair instead. Maintained for backward compatibility."""
    
    google_calendar_id: str = Field(..., description="Google calendar ID")
    icloud_calendar_id: str = Field(..., description="iCloud calendar ID")
    google_calendar_name: Optional[str] = Field(None, description="Google calendar name")
    icloud_calendar_name: Optional[str] = Field(None, description="iCloud calendar name")
    bidirectional: bool = Field(True, description="Whether sync is bidirectional")
    sync_direction: Optional[str] = Field(None, description="Sync direction if not bidirectional")
    enabled: bool = Field(True, description="Whether this mapping is enabled")
    conflict_resolution: Optional[ConflictResolution] = Field(None, description="Override global conflict resolution")
    
    def to_calendar_pair(self) -> CalendarPair:
        """Convert to new CalendarPair format."""
        return CalendarPair(
            google_calendar_id=self.google_calendar_id,
            icloud_calendar_id=self.icloud_calendar_id,
            google_calendar_name=self.google_calendar_name,
            icloud_calendar_name=self.icloud_calendar_name,
            bidirectional=self.bidirectional,
            sync_direction=self.sync_direction,
            enabled=self.enabled,
            conflict_resolution=self.conflict_resolution
        )


class SyncConfiguration(BaseModel):
    """Sync configuration model."""
    
    sync_interval_minutes: int = Field(30, ge=1)
    conflict_resolution: ConflictResolution = Field(ConflictResolution.MANUAL)
    max_events_per_sync: int = Field(1000, ge=1)
    sync_past_days: int = Field(30, ge=0)
    sync_future_days: int = Field(365, ge=0)
    retry_attempts: int = Field(3, ge=1)
    retry_delay_seconds: int = Field(5, ge=1)
    enable_webhooks: bool = Field(False)
    webhook_port: int = Field(8080, ge=1024, le=65535)
    
    # Explicit calendar pairs - no cross product sync!
    calendar_pairs: List[CalendarPair] = Field(default_factory=list, description="Explicit one-to-one calendar pairs")
    
    # Auto-pairing settings (only used when no explicit pairs are configured)
    auto_create_pairs: bool = Field(False, description="Auto-create pairs by matching calendar names")
    name_matching_strategy: str = Field("exact", description="Name matching strategy: 'exact', 'fuzzy', or 'manual'")
    
    # Legacy support (DEPRECATED - will be removed in v3.0)
    calendar_mappings: List[CalendarMapping] = Field(default_factory=list, description="DEPRECATED: use calendar_pairs")
    selected_google_calendars: List[str] = Field(default_factory=list, description="DEPRECATED: use calendar_pairs")
    selected_icloud_calendars: List[str] = Field(default_factory=list, description="DEPRECATED: use calendar_pairs")
    
    @validator('calendar_pairs')
    def validate_calendar_pairs(cls, v):
        """Validate that calendar pairs don't have duplicate calendar IDs."""
        google_ids = [pair.google_calendar_id for pair in v if pair.enabled]
        icloud_ids = [pair.icloud_calendar_id for pair in v if pair.enabled]
        
        if len(google_ids) != len(set(google_ids)):
            raise ValueError("Duplicate Google calendar IDs found in calendar pairs")
        if len(icloud_ids) != len(set(icloud_ids)):
            raise ValueError("Duplicate iCloud calendar IDs found in calendar pairs")
        
        return v
    
    def get_active_pairs(self) -> List[CalendarPair]:
        """Get only enabled calendar pairs."""
        return [pair for pair in self.calendar_pairs if pair.enabled]
    
    def has_explicit_pairs(self) -> bool:
        """Check if explicit calendar pairs are configured."""
        return bool(self.calendar_pairs)


T = TypeVar('T')

@dataclass
class ChangeSet(Generic[T]):
    changed: Dict[str, T]
    deleted_native_ids: Set[str]
    next_sync_token: Optional[str]
    used_sync_token: bool
    invalid_token_used: Optional[str] = None