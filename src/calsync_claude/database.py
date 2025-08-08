"""Database models and operations for sync state management."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import create_engine, Column, String, DateTime, Boolean, Text, Integer, ForeignKey
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
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    
    # Relationships
    event_mappings = relationship("EventMappingDB", back_populates="calendar_mapping")


class EventMappingDB(Base):
    """Database model for event mappings between services."""
    
    __tablename__ = 'event_mappings'
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    calendar_mapping_id = Column(GUID(), ForeignKey('calendar_mappings.id'), nullable=False, index=True)
    google_event_id = Column(String(255), nullable=True, index=True)
    icloud_event_id = Column(String(255), nullable=True, index=True)
    google_calendar_id = Column(String(255), nullable=True, index=True)
    icloud_calendar_id = Column(String(500), nullable=True, index=True)
    google_etag = Column(String(255), nullable=True)
    icloud_etag = Column(String(255), nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))
    last_sync_at = Column(DateTime, nullable=True)
    sync_direction = Column(String(20), nullable=True)  # 'google_to_icloud', 'icloud_to_google'
    
    # Relationships
    calendar_mapping = relationship("CalendarMappingDB", back_populates="event_mappings")
    sync_operations = relationship("SyncOperationDB", back_populates="event_mapping")


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


class ConfigDB(Base):
    """Database model for configuration storage."""
    
    __tablename__ = 'config'
    
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(pytz.UTC))


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
        sync_direction: Optional[str] = None
    ) -> EventMappingDB:
        """Create new event mapping.
        
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
            
        Returns:
            Created event mapping
        """
        mapping = EventMappingDB(
            google_event_id=google_event_id,
            icloud_event_id=icloud_event_id,
            google_calendar_id=google_calendar_id,
            icloud_calendar_id=icloud_calendar_id,
            google_etag=google_etag,
            icloud_etag=icloud_etag,
            content_hash=content_hash,
            sync_direction=sync_direction,
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
        sync_direction: Optional[str] = None
    ) -> EventMappingDB:
        """Update event mapping.
        
        Args:
            session: Database session
            mapping: Event mapping to update
            google_event_id: Google event ID
            icloud_event_id: iCloud event ID
            google_etag: Google event ETag
            icloud_etag: iCloud event ETag
            content_hash: Event content hash
            sync_direction: Sync direction
            
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