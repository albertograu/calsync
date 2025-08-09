"""Database models and operations for sync state management."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import create_engine, Column, String, DateTime, Boolean, Text, Integer, ForeignKey, Index, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.types import TypeDecorator, CHAR
import pytz

from .config import Settings

Base = declarative_base()


class GUID(TypeDecorator):
    """Platform-independent GUID type."""
    
    impl = CHAR
    cache_ok = True
    
    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PostgresUUID())
        else:
            return dialect.type_descriptor(CHAR(32))
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, UUID):
                return "%.32x" % UUID(value).int
            else:
                return "%.32x" % value.int
    
    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, UUID):
                return UUID(value)
            return value


class CalendarMappingDB(Base):
    """Database model for calendar mappings between Google and iCloud."""
    
    __tablename__ = 'calendar_mappings'
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    google_calendar_id = Column(String(255), nullable=False, index=True)
    icloud_calendar_id = Column(String(500), nullable=False, index=True)
    google_calendar_name = Column(String(255), nullable=True)
    icloud_calendar_name = Column(String(255), nullable=True)
    bidirectional = Column(Boolean, nullable=False, default=True)
    sync_direction = Column(String(20), nullable=True)  # 'google_to_icloud', 'icloud_to_google'
    enabled = Column(Boolean, nullable=False, default=True)
    conflict_resolution = Column(String(20), nullable=True)  # Override global setting
    
    # Sync tokens for incremental sync (CRITICAL for production)
    google_sync_token = Column(String(1000), nullable=True)            # Google nextSyncToken
    icloud_sync_token = Column(String(1000), nullable=True)            # iCloud CTag or sync-token
    google_last_updated = Column(DateTime, nullable=True)              # Last successful Google sync
    icloud_last_updated = Column(DateTime, nullable=True)              # Last successful iCloud sync
    
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    
    # Relationships
    event_mappings = relationship("EventMappingDB", back_populates="calendar_mapping")
    
    # Constraints and indexes
    __table_args__ = (
        # Unique constraint on calendar pair
        UniqueConstraint('google_calendar_id', 'icloud_calendar_id', name='uq_calendar_mapping_pair'),
        # Indexes for performance
        Index('idx_calendar_mapping_google', 'google_calendar_id'),
        Index('idx_calendar_mapping_icloud', 'icloud_calendar_id'),
        Index('idx_calendar_mapping_enabled', 'enabled'),
        Index('idx_calendar_mapping_sync_tokens', 'google_sync_token', 'icloud_sync_token'),
    )


class EventMappingDB(Base):
    """Database model for event mappings between services."""
    
    __tablename__ = 'event_mappings'
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    calendar_mapping_id = Column(GUID(), ForeignKey('calendar_mappings.id'), nullable=False, index=True)
    
    # Event IDs (service-specific)
    google_event_id = Column(String(255), nullable=True, index=True)
    icloud_event_id = Column(String(255), nullable=True, index=True)
    
    # Calendar IDs  
    google_calendar_id = Column(String(255), nullable=True, index=True)
    icloud_calendar_id = Column(String(500), nullable=True, index=True)
    
    # UIDs for cross-platform matching (CRITICAL for production)
    google_ical_uid = Column(String(255), nullable=True, index=True)  # Google's iCalUID
    icloud_uid = Column(String(255), nullable=True, index=True)       # iCloud's UID
    event_uid = Column(String(255), nullable=True, index=True)        # Canonical UID for deduplication
    
    # Resource paths for direct access (avoids scanning)
    icloud_resource_url = Column(String(1000), nullable=True)         # Full CalDAV resource URL
    google_self_link = Column(String(1000), nullable=True)            # Google API self link
    
    # ETags and versioning
    google_etag = Column(String(255), nullable=True)
    icloud_etag = Column(String(255), nullable=True)
    google_sequence = Column(Integer, nullable=True, default=0)       # Google sequence number
    icloud_sequence = Column(Integer, nullable=True, default=0)       # iCloud sequence number
    
    # Content tracking
    content_hash = Column(String(64), nullable=False, index=True)
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    last_sync_at = Column(DateTime, nullable=True)
    
    # Sync metadata
    sync_direction = Column(String(20), nullable=True)  # 'google_to_icloud', 'icloud_to_google'
    sync_status = Column(String(20), nullable=True, default='active')  # 'active', 'deleted', 'orphaned'
    
    # Relationships
    calendar_mapping = relationship("CalendarMappingDB", back_populates="event_mappings")
    sync_operations = relationship("SyncOperationDB", back_populates="event_mapping")
    
    # Constraints and indexes
    __table_args__ = (
        # Unique constraints for event IDs within calendar mapping
        UniqueConstraint('calendar_mapping_id', 'google_event_id', name='uq_event_mapping_google'),
        UniqueConstraint('calendar_mapping_id', 'icloud_event_id', name='uq_event_mapping_icloud'),
        
        # Performance indexes
        Index('idx_event_mapping_calendar', 'calendar_mapping_id'),
        Index('idx_event_mapping_google_id', 'google_event_id'),
        Index('idx_event_mapping_icloud_id', 'icloud_event_id'),
        Index('idx_event_mapping_google_cal', 'google_calendar_id'),
        Index('idx_event_mapping_icloud_cal', 'icloud_calendar_id'),
        
        # CRITICAL: UID indexes for production matching
        Index('idx_event_mapping_google_ical_uid', 'google_ical_uid'),
        Index('idx_event_mapping_icloud_uid', 'icloud_uid'),
        Index('idx_event_mapping_event_uid', 'event_uid'),
        
        # Status and content indexes
        Index('idx_event_mapping_sync_status', 'sync_status'),
        Index('idx_event_mapping_content_hash', 'content_hash'),
        Index('idx_event_mapping_last_sync', 'last_sync_at'),
        
        # Composite indexes for common queries
        Index('idx_event_mapping_calendar_status', 'calendar_mapping_id', 'sync_status'),
        Index('idx_event_mapping_uid_status', 'event_uid', 'sync_status'),
    )


class SyncSessionDB(Base):
    """Database model for sync sessions."""
    
    __tablename__ = 'sync_sessions'
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    completed_at = Column(DateTime, nullable=True)
    dry_run = Column(Boolean, nullable=False, default=False)
    
    # Counters
    google_to_icloud_created = Column(Integer, default=0)
    google_to_icloud_updated = Column(Integer, default=0)
    google_to_icloud_deleted = Column(Integer, default=0)
    google_to_icloud_skipped = Column(Integer, default=0)
    
    icloud_to_google_created = Column(Integer, default=0)
    icloud_to_google_updated = Column(Integer, default=0)
    icloud_to_google_deleted = Column(Integer, default=0)
    icloud_to_google_skipped = Column(Integer, default=0)
    
    # Status
    status = Column(String(20), nullable=False, default='running')  # 'running', 'completed', 'failed'
    error_message = Column(Text, nullable=True)
    
    # Relationships
    sync_operations = relationship("SyncOperationDB", back_populates="sync_session")
    conflicts = relationship("ConflictDB", back_populates="sync_session")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_sync_session_started', 'started_at'),
        Index('idx_sync_session_completed', 'completed_at'),
        Index('idx_sync_session_status', 'status'),
        Index('idx_sync_session_dry_run', 'dry_run'),
    )


class SyncOperationDB(Base):
    """Database model for individual sync operations."""
    
    __tablename__ = 'sync_operations'
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    sync_session_id = Column(GUID(), ForeignKey('sync_sessions.id'), nullable=False)
    event_mapping_id = Column(GUID(), ForeignKey('event_mappings.id'), nullable=True)
    
    operation = Column(String(20), nullable=False)  # 'create', 'update', 'delete', 'skip'
    source = Column(String(20), nullable=False)  # 'google', 'icloud'
    target = Column(String(20), nullable=False)  # 'google', 'icloud'
    event_id = Column(String(255), nullable=False)
    event_summary = Column(String(500), nullable=True)
    
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    
    # Relationships
    sync_session = relationship("SyncSessionDB", back_populates="sync_operations")
    event_mapping = relationship("EventMappingDB", back_populates="sync_operations")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_sync_operation_session', 'sync_session_id'),
        Index('idx_sync_operation_mapping', 'event_mapping_id'),
        Index('idx_sync_operation_timestamp', 'timestamp'),
        Index('idx_sync_operation_success', 'success'),
        Index('idx_sync_operation_type', 'operation', 'source', 'target'),
    )


class ConflictDB(Base):
    """Database model for sync conflicts."""
    
    __tablename__ = 'conflicts'
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    sync_session_id = Column(GUID(), ForeignKey('sync_sessions.id'), nullable=False)
    
    google_event_id = Column(String(255), nullable=True)
    icloud_event_id = Column(String(255), nullable=True)
    google_event_data = Column(Text, nullable=True)  # JSON
    icloud_event_data = Column(Text, nullable=True)  # JSON
    
    conflict_type = Column(String(50), nullable=False)  # 'content_mismatch', 'both_modified', etc.
    resolution = Column(String(50), nullable=True)  # 'manual', 'google_wins', 'icloud_wins', etc.
    resolved = Column(Boolean, nullable=False, default=False)
    resolved_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    
    # Relationships
    sync_session = relationship("SyncSessionDB", back_populates="conflicts")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_conflict_session', 'sync_session_id'),
        Index('idx_conflict_resolved', 'resolved'),
        Index('idx_conflict_type', 'conflict_type'),
        Index('idx_conflict_created', 'created_at'),
        Index('idx_conflict_unresolved', 'resolved', 'created_at'),
    )


class ConfigDB(Base):
    """Database model for configuration storage."""
    
    __tablename__ = 'config'
    
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_config_updated', 'updated_at'),
    )


class DatabaseManager:
    """Database manager for sync operations."""
    
    def __init__(self, settings: Settings):
        """Initialize database manager.
        
        Args:
            settings: Application settings
        """
        self.settings = settings
        self.engine = create_engine(
            settings.database_url,
            echo=settings.debug,
            pool_pre_ping=True
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
    
    def init_db(self) -> None:
        """Initialize database tables."""
        Base.metadata.create_all(bind=self.engine)
    
    def get_session(self) -> Session:
        """Get database session."""
        return self.SessionLocal()
    
    def get_event_mapping(
        self, 
        session: Session,
        google_event_id: Optional[str] = None,
        icloud_event_id: Optional[str] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by event IDs.
        
        Args:
            session: Database session
            google_event_id: Google event ID
            icloud_event_id: iCloud event ID
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB)
        
        if google_event_id:
            query = query.filter(EventMappingDB.google_event_id == google_event_id)
        if icloud_event_id:
            query = query.filter(EventMappingDB.icloud_event_id == icloud_event_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def get_event_mapping_by_uid(
        self,
        session: Session,
        event_uid: str,
        calendar_mapping_id: Optional[UUID] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by canonical UID (CRITICAL for production deduplication).
        
        Args:
            session: Database session
            event_uid: Canonical event UID
            calendar_mapping_id: Optional calendar mapping ID to scope search
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid == event_uid,
            EventMappingDB.sync_status == 'active'
        )
        
        if calendar_mapping_id:
            query = query.filter(EventMappingDB.calendar_mapping_id == calendar_mapping_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def get_event_mapping_by_google_ical_uid(
        self,
        session: Session,
        google_ical_uid: str,
        calendar_mapping_id: Optional[UUID] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by Google iCalUID (CRITICAL for cross-platform matching).
        
        Args:
            session: Database session
            google_ical_uid: Google's iCalUID
            calendar_mapping_id: Optional calendar mapping ID to scope search
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.google_ical_uid == google_ical_uid,
            EventMappingDB.sync_status == 'active'
        )
        
        if calendar_mapping_id:
            query = query.filter(EventMappingDB.calendar_mapping_id == calendar_mapping_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def get_event_mapping_by_icloud_uid(
        self,
        session: Session,
        icloud_uid: str,
        calendar_mapping_id: Optional[UUID] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by iCloud UID (CRITICAL for cross-platform matching).
        
        Args:
            session: Database session
            icloud_uid: iCloud's UID field
            calendar_mapping_id: Optional calendar mapping ID to scope search
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.icloud_uid == icloud_uid,
            EventMappingDB.sync_status == 'active'
        )
        
        if calendar_mapping_id:
            query = query.filter(EventMappingDB.calendar_mapping_id == calendar_mapping_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def create_event_mapping(
        self,
        session: Session,
        google_event_id: Optional[str] = None,
        icloud_event_id: Optional[str] = None,
        google_calendar_id: Optional[str] = None,
        icloud_calendar_id: Optional[str] = None,
        google_etag: Optional[str] = None,
        icloud_etag: Optional[str] = None,
        content_hash: str = "",
        sync_direction: Optional[str] = None,
        # CRITICAL: Add UID fields for production reliability
        google_ical_uid: Optional[str] = None,
        icloud_uid: Optional[str] = None,
        event_uid: Optional[str] = None,
        # Resource paths for direct access
        icloud_resource_url: Optional[str] = None,
        google_self_link: Optional[str] = None,
        # Sequences for conflict resolution
        google_sequence: Optional[int] = None,
        icloud_sequence: Optional[int] = None,
        # Status tracking
        sync_status: str = 'active',
        calendar_mapping_id: Optional[str] = None
    ) -> EventMappingDB:
        """Create new event mapping with all production-critical fields.
        
        Args:
            session: Database session
            google_event_id: Google event ID
            icloud_event_id: iCloud event ID
            google_calendar_id: Google calendar ID
            icloud_calendar_id: iCloud calendar ID
            google_etag: Google event ETag
            icloud_etag: iCloud event ETag
            content_hash: Event content hash
            sync_direction: Sync direction
            google_ical_uid: Google's iCalUID for cross-platform matching
            icloud_uid: iCloud's UID field
            event_uid: Canonical UID for deduplication
            icloud_resource_url: Full CalDAV resource URL for direct access
            google_self_link: Google API self link
            google_sequence: Google sequence for conflict resolution
            icloud_sequence: iCloud sequence for conflict resolution
            sync_status: Sync status (active/deleted/orphaned)
            calendar_mapping_id: Calendar mapping ID
            
        Returns:
            Created event mapping
        """
        mapping = EventMappingDB(
            calendar_mapping_id=calendar_mapping_id,
            google_event_id=google_event_id,
            icloud_event_id=icloud_event_id,
            google_calendar_id=google_calendar_id,
            icloud_calendar_id=icloud_calendar_id,
            # UIDs for cross-platform matching (CRITICAL)
            google_ical_uid=google_ical_uid,
            icloud_uid=icloud_uid,
            event_uid=event_uid,
            # Resource paths for direct access
            icloud_resource_url=icloud_resource_url,
            google_self_link=google_self_link,
            # ETags and sequences
            google_etag=google_etag,
            icloud_etag=icloud_etag,
            google_sequence=google_sequence or 0,
            icloud_sequence=icloud_sequence or 0,
            content_hash=content_hash,
            sync_direction=sync_direction,
            sync_status=sync_status,
            last_sync_at=datetime.now(pytz.UTC)
        )
        
        session.add(mapping)
        session.commit()
        return mapping
    
    def update_event_mapping(
        self,
        session: Session,
        mapping: EventMappingDB,
        google_event_id: Optional[str] = None,
        icloud_event_id: Optional[str] = None,
        google_etag: Optional[str] = None,
        icloud_etag: Optional[str] = None,
        content_hash: Optional[str] = None,
        sync_direction: Optional[str] = None,
        # CRITICAL: Add UID fields for production updates
        google_ical_uid: Optional[str] = None,
        icloud_uid: Optional[str] = None,
        event_uid: Optional[str] = None,
        # Resource paths
        icloud_resource_url: Optional[str] = None,
        google_self_link: Optional[str] = None,
        # Sequences
        google_sequence: Optional[int] = None,
        icloud_sequence: Optional[int] = None,
        # Status
        sync_status: Optional[str] = None
    ) -> EventMappingDB:
        """Update event mapping with all production-critical fields.
        
        Args:
            session: Database session
            mapping: Event mapping to update
            google_event_id: Google event ID
            icloud_event_id: iCloud event ID
            google_etag: Google event ETag
            icloud_etag: iCloud event ETag
            content_hash: Event content hash
            sync_direction: Sync direction
            google_ical_uid: Google's iCalUID for cross-platform matching
            icloud_uid: iCloud's UID field
            event_uid: Canonical UID for deduplication
            icloud_resource_url: Full CalDAV resource URL for direct access
            google_self_link: Google API self link
            google_sequence: Google sequence for conflict resolution
            icloud_sequence: iCloud sequence for conflict resolution
            sync_status: Sync status (active/deleted/orphaned)
            
        Returns:
            Updated event mapping
        """
        if google_event_id is not None:
            mapping.google_event_id = google_event_id
        if icloud_event_id is not None:
            mapping.icloud_event_id = icloud_event_id
        if google_etag is not None:
            mapping.google_etag = google_etag
        if icloud_etag is not None:
            mapping.icloud_etag = icloud_etag
        if content_hash is not None:
            mapping.content_hash = content_hash
        if sync_direction is not None:
            mapping.sync_direction = sync_direction
        
        # Update UID fields (CRITICAL for production)
        if google_ical_uid is not None:
            mapping.google_ical_uid = google_ical_uid
        if icloud_uid is not None:
            mapping.icloud_uid = icloud_uid
        if event_uid is not None:
            mapping.event_uid = event_uid
        
        # Update resource paths for direct access
        if icloud_resource_url is not None:
            mapping.icloud_resource_url = icloud_resource_url
        if google_self_link is not None:
            mapping.google_self_link = google_self_link
        
        # Update sequences for conflict resolution
        if google_sequence is not None:
            mapping.google_sequence = google_sequence
        if icloud_sequence is not None:
            mapping.icloud_sequence = icloud_sequence
        
        # Update status
        if sync_status is not None:
            mapping.sync_status = sync_status
        
        mapping.updated_at = datetime.now(pytz.UTC)
        mapping.last_sync_at = datetime.now(pytz.UTC)
        
        session.commit()
        return mapping
    
    def create_sync_session(
        self,
        session: Session,
        dry_run: bool = False
    ) -> SyncSessionDB:
        """Create new sync session.
        
        Args:
            session: Database session
            dry_run: Whether this is a dry run
            
        Returns:
            Created sync session
        """
        sync_session = SyncSessionDB(dry_run=dry_run)
        session.add(sync_session)
        session.commit()
        return sync_session
    
    def complete_sync_session(
        self,
        session: Session,
        sync_session: SyncSessionDB,
        status: str = 'completed',
        error_message: Optional[str] = None
    ) -> SyncSessionDB:
        """Complete sync session.
        
        Args:
            session: Database session
            sync_session: Sync session to complete
            status: Final status
            error_message: Error message if failed
            
        Returns:
            Updated sync session
        """
        sync_session.completed_at = datetime.now(pytz.UTC)
        sync_session.status = status
        if error_message:
            sync_session.error_message = error_message
        
        session.commit()
        return sync_session
    
    def create_sync_operation(
        self,
        session: Session,
        sync_session: SyncSessionDB,
        operation: str,
        source: str,
        target: str,
        event_id: str,
        event_summary: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        event_mapping: Optional[EventMappingDB] = None
    ) -> SyncOperationDB:
        """Create sync operation record.
        
        Args:
            session: Database session
            sync_session: Parent sync session
            operation: Operation type
            source: Source service
            target: Target service
            event_id: Event ID
            event_summary: Event summary
            success: Whether operation succeeded
            error_message: Error message if failed
            event_mapping: Associated event mapping
            
        Returns:
            Created sync operation
        """
        sync_op = SyncOperationDB(
            sync_session_id=sync_session.id,
            event_mapping_id=event_mapping.id if event_mapping else None,
            operation=operation,
            source=source,
            target=target,
            event_id=event_id,
            event_summary=event_summary,
            success=success,
            error_message=error_message
        )
        
        session.add(sync_op)
        session.commit()
        return sync_op
    
    def create_conflict(
        self,
        session: Session,
        sync_session: SyncSessionDB,
        conflict_type: str,
        google_event_id: Optional[str] = None,
        icloud_event_id: Optional[str] = None,
        google_event_data: Optional[str] = None,
        icloud_event_data: Optional[str] = None
    ) -> ConflictDB:
        """Create conflict record.
        
        Args:
            session: Database session
            sync_session: Parent sync session
            conflict_type: Type of conflict
            google_event_id: Google event ID
            icloud_event_id: iCloud event ID
            google_event_data: Google event data as JSON
            icloud_event_data: iCloud event data as JSON
            
        Returns:
            Created conflict
        """
        conflict = ConflictDB(
            sync_session_id=sync_session.id,
            google_event_id=google_event_id,
            icloud_event_id=icloud_event_id,
            google_event_data=google_event_data,
            icloud_event_data=icloud_event_data,
            conflict_type=conflict_type
        )
        
        session.add(conflict)
        session.commit()
        return conflict
    
    def get_recent_sync_sessions(
        self,
        session: Session,
        limit: int = 10
    ) -> List[SyncSessionDB]:
        """Get recent sync sessions.
        
        Args:
            session: Database session
            limit: Number of sessions to return
            
        Returns:
            List of sync sessions
        """
        return session.query(SyncSessionDB).order_by(
            SyncSessionDB.started_at.desc()
        ).limit(limit).all()
    
    def get_unresolved_conflicts(
        self,
        session: Session
    ) -> List[ConflictDB]:
        """Get unresolved conflicts.
        
        Args:
            session: Database session
            
        Returns:
            List of unresolved conflicts
        """
        return session.query(ConflictDB).filter(
            ConflictDB.resolved == False
        ).order_by(ConflictDB.created_at.desc()).all()
    
    def get_calendar_mappings(self, session: Session) -> List[CalendarMappingDB]:
        """Get all calendar mappings.
        
        Args:
            session: Database session
            
        Returns:
            List of calendar mappings
        """
        return session.query(CalendarMappingDB).filter(
            CalendarMappingDB.enabled == True
        ).order_by(CalendarMappingDB.created_at).all()
    
    def get_calendar_mapping(
        self,
        session: Session,
        google_calendar_id: str,
        icloud_calendar_id: str
    ) -> Optional[CalendarMappingDB]:
        """Get a specific calendar mapping.
        
        Args:
            session: Database session
            google_calendar_id: Google calendar ID
            icloud_calendar_id: iCloud calendar ID
            
        Returns:
            Calendar mapping or None
        """
        return session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_calendar_id == google_calendar_id,
            CalendarMappingDB.icloud_calendar_id == icloud_calendar_id
        ).first()
    
    def create_calendar_mapping(
        self,
        session: Session,
        google_calendar_id: str,
        icloud_calendar_id: str,
        google_calendar_name: Optional[str] = None,
        icloud_calendar_name: Optional[str] = None,
        bidirectional: bool = True,
        sync_direction: Optional[str] = None,
        enabled: bool = True,
        conflict_resolution: Optional[str] = None
    ) -> CalendarMappingDB:
        """Create a new calendar mapping.
        
        Args:
            session: Database session
            google_calendar_id: Google calendar ID
            icloud_calendar_id: iCloud calendar ID
            google_calendar_name: Google calendar name
            icloud_calendar_name: iCloud calendar name
            bidirectional: Whether sync is bidirectional
            sync_direction: Sync direction if not bidirectional
            enabled: Whether mapping is enabled
            conflict_resolution: Override conflict resolution
            
        Returns:
            Created calendar mapping
        """
        mapping = CalendarMappingDB(
            google_calendar_id=google_calendar_id,
            icloud_calendar_id=icloud_calendar_id,
            google_calendar_name=google_calendar_name,
            icloud_calendar_name=icloud_calendar_name,
            bidirectional=bidirectional,
            sync_direction=sync_direction,
            enabled=enabled,
            conflict_resolution=conflict_resolution
        )
        
        session.add(mapping)
        session.commit()
        return mapping
    
    def update_calendar_mapping(
        self,
        session: Session,
        mapping: CalendarMappingDB,
        **kwargs
    ) -> CalendarMappingDB:
        """Update calendar mapping.
        
        Args:
            session: Database session
            mapping: Calendar mapping to update
            **kwargs: Fields to update
            
        Returns:
            Updated calendar mapping
        """
        for key, value in kwargs.items():
            if hasattr(mapping, key):
                setattr(mapping, key, value)
        
        mapping.updated_at = datetime.now(pytz.UTC)
        session.commit()
        return mapping
    
    def delete_calendar_mapping(
        self,
        session: Session,
        mapping: CalendarMappingDB
    ) -> None:
        """Delete calendar mapping.
        
        Args:
            session: Database session
            mapping: Calendar mapping to delete
        """
        session.delete(mapping)
        session.commit()
    
    def get_event_mapping_by_calendar(
        self, 
        session: Session,
        calendar_mapping_id: UUID,
        google_event_id: Optional[str] = None,
        icloud_event_id: Optional[str] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by calendar mapping and event IDs.
        
        Args:
            session: Database session
            calendar_mapping_id: Calendar mapping ID
            google_event_id: Google event ID
            icloud_event_id: iCloud event ID
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.calendar_mapping_id == calendar_mapping_id
        )
        
        if google_event_id:
            query = query.filter(EventMappingDB.google_event_id == google_event_id)
        if icloud_event_id:
            query = query.filter(EventMappingDB.icloud_event_id == icloud_event_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def get_event_mapping_by_uid(
        self,
        session: Session,
        event_uid: str,
        calendar_mapping_id: Optional[UUID] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by canonical UID (CRITICAL for production deduplication).
        
        Args:
            session: Database session
            event_uid: Canonical event UID
            calendar_mapping_id: Optional calendar mapping ID to scope search
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid == event_uid,
            EventMappingDB.sync_status == 'active'
        )
        
        if calendar_mapping_id:
            query = query.filter(EventMappingDB.calendar_mapping_id == calendar_mapping_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def get_event_mapping_by_google_ical_uid(
        self,
        session: Session,
        google_ical_uid: str,
        calendar_mapping_id: Optional[UUID] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by Google iCalUID (CRITICAL for cross-platform matching).
        
        Args:
            session: Database session
            google_ical_uid: Google's iCalUID
            calendar_mapping_id: Optional calendar mapping ID to scope search
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.google_ical_uid == google_ical_uid,
            EventMappingDB.sync_status == 'active'
        )
        
        if calendar_mapping_id:
            query = query.filter(EventMappingDB.calendar_mapping_id == calendar_mapping_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }
    
    def get_event_mapping_by_icloud_uid(
        self,
        session: Session,
        icloud_uid: str,
        calendar_mapping_id: Optional[UUID] = None
    ) -> Optional[EventMappingDB]:
        """Get event mapping by iCloud UID (CRITICAL for cross-platform matching).
        
        Args:
            session: Database session
            icloud_uid: iCloud's UID field
            calendar_mapping_id: Optional calendar mapping ID to scope search
            
        Returns:
            Event mapping or None if not found
        """
        query = session.query(EventMappingDB).filter(
            EventMappingDB.icloud_uid == icloud_uid,
            EventMappingDB.sync_status == 'active'
        )
        
        if calendar_mapping_id:
            query = query.filter(EventMappingDB.calendar_mapping_id == calendar_mapping_id)
        
        return query.first()
    
    def get_sync_statistics(self, session: Session, days: int = 30) -> Dict[str, Any]:
        """Get synchronization statistics for the past N days."""
        from datetime import timedelta
        
        cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
        
        sessions = session.query(SyncSessionDB).filter(
            SyncSessionDB.started_at >= cutoff_date
        ).all()
        
        operations = session.query(SyncOperationDB).filter(
            SyncOperationDB.timestamp >= cutoff_date
        ).all()
        
        return {
            'period_days': days,
            'total_sessions': len(sessions),
            'successful_sessions': len([s for s in sessions if s.status == 'completed']),
            'failed_sessions': len([s for s in sessions if s.status == 'failed']),
            'total_operations': len(operations),
            'successful_operations': len([o for o in operations if o.success]),
            'failed_operations': len([o for o in operations if not o.success])
        }
    
    def validate_database_integrity(self, session: Session) -> Dict[str, Any]:
        """Validate database integrity and return health report."""
        issues = []
        
        # Check for mappings without UIDs
        mappings_without_uid = session.query(EventMappingDB).filter(
            EventMappingDB.event_uid.is_(None),
            EventMappingDB.google_ical_uid.is_(None),
            EventMappingDB.icloud_uid.is_(None),
            EventMappingDB.sync_status == 'active'
        ).count()
        
        if mappings_without_uid > 0:
            issues.append(f"{mappings_without_uid} active event mappings without any UID")
        
        # Check for calendar mappings without sync tokens
        mappings_without_tokens = session.query(CalendarMappingDB).filter(
            CalendarMappingDB.google_sync_token.is_(None),
            CalendarMappingDB.icloud_sync_token.is_(None),
            CalendarMappingDB.enabled == True
        ).count()
        
        if mappings_without_tokens > 0:
            issues.append(f"{mappings_without_tokens} enabled calendar mappings without sync tokens")
        
        return {
            'healthy': len(issues) == 0,
            'issues': issues,
            'total_calendar_mappings': session.query(CalendarMappingDB).count(),
            'total_event_mappings': session.query(EventMappingDB).count(),
            'active_event_mappings': session.query(EventMappingDB).filter(
                EventMappingDB.sync_status == 'active'
            ).count()
        }