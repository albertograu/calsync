"""Data models for calendar synchronization."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, List
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
    source: EventSource = Field(..., description="Source service")
    summary: str = Field("", description="Event title/summary")
    description: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Event location")
    start: datetime = Field(..., description="Event start time")
    end: datetime = Field(..., description="Event end time")
    all_day: bool = Field(False, description="Whether event is all-day")
    created: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    updated: datetime = Field(default_factory=lambda: datetime.now(pytz.UTC))
    etag: Optional[str] = Field(None, description="ETag for change detection")
    recurring_event_id: Optional[str] = Field(None, description="Recurring event ID")
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
            raise ValueError('End time must be after start time')
        return v
    
    def content_hash(self) -> str:
        """Generate content hash for change detection."""
        import hashlib
        import json
        
        content = {
            'summary': self.summary,
            'description': self.description or '',
            'location': self.location or '',
            'start': self.start.isoformat(),
            'end': self.end.isoformat(),
            'all_day': self.all_day,
        }
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()


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
    selected_google_calendars: List[str] = Field(default_factory=list)
    selected_icloud_calendars: List[str] = Field(default_factory=list)