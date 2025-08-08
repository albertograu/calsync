"""Tests for data models."""

import pytest
from datetime import datetime, timedelta
from uuid import UUID

import pytz

from calsync_claude.models import (
    CalendarEvent, EventSource, EventMapping, SyncResult, 
    SyncReport, SyncOperation, ConflictResolution
)


class TestCalendarEvent:
    """Tests for CalendarEvent model."""
    
    def test_create_basic_event(self):
        """Test creating a basic calendar event."""
        start = datetime.now(pytz.UTC)
        end = start + timedelta(hours=1)
        
        event = CalendarEvent(
            id="test-123",
            source=EventSource.GOOGLE,
            summary="Test Event",
            start=start,
            end=end
        )
        
        assert event.id == "test-123"
        assert event.source == EventSource.GOOGLE
        assert event.summary == "Test Event"
        assert event.start == start
        assert event.end == end
        assert not event.all_day
    
    def test_timezone_validation(self):
        """Test timezone validation for datetime fields."""
        # Naive datetime should be converted to UTC
        naive_start = datetime(2023, 12, 1, 10, 0, 0)
        naive_end = datetime(2023, 12, 1, 11, 0, 0)
        
        event = CalendarEvent(
            id="test-123",
            source=EventSource.GOOGLE,
            summary="Test Event",
            start=naive_start,
            end=naive_end
        )
        
        # Should have UTC timezone
        assert event.start.tzinfo == pytz.UTC
        assert event.end.tzinfo == pytz.UTC
    
    def test_end_after_start_validation(self):
        """Test that end time must be after start time."""
        start = datetime.now(pytz.UTC)
        end = start - timedelta(hours=1)  # End before start
        
        with pytest.raises(ValueError, match="End time must be after start time"):
            CalendarEvent(
                id="test-123",
                source=EventSource.GOOGLE,
                summary="Test Event",
                start=start,
                end=end
            )
    
    def test_content_hash(self):
        """Test content hash generation."""
        start = datetime(2023, 12, 1, 10, 0, 0, tzinfo=pytz.UTC)
        end = datetime(2023, 12, 1, 11, 0, 0, tzinfo=pytz.UTC)
        
        event1 = CalendarEvent(
            id="test-123",
            source=EventSource.GOOGLE,
            summary="Test Event",
            description="Test Description",
            location="Test Location",
            start=start,
            end=end
        )
        
        event2 = CalendarEvent(
            id="test-456",  # Different ID
            source=EventSource.ICLOUD,  # Different source
            summary="Test Event",
            description="Test Description",
            location="Test Location",
            start=start,
            end=end
        )
        
        # Same content should generate same hash
        assert event1.content_hash() == event2.content_hash()
        
        # Different content should generate different hash
        event3 = CalendarEvent(
            id="test-123",
            source=EventSource.GOOGLE,
            summary="Different Event",  # Different summary
            start=start,
            end=end
        )
        
        assert event1.content_hash() != event3.content_hash()
    
    def test_all_day_event(self):
        """Test all-day event creation."""
        start = datetime(2023, 12, 1, tzinfo=pytz.UTC)
        end = datetime(2023, 12, 2, tzinfo=pytz.UTC)
        
        event = CalendarEvent(
            id="test-123",
            source=EventSource.GOOGLE,
            summary="All Day Event",
            start=start,
            end=end,
            all_day=True
        )
        
        assert event.all_day
        assert event.content_hash()  # Should generate hash correctly


class TestEventMapping:
    """Tests for EventMapping model."""
    
    def test_create_mapping(self):
        """Test creating event mapping."""
        mapping = EventMapping(
            google_event_id="google-123",
            icloud_event_id="icloud-456",
            content_hash="hash123"
        )
        
        assert isinstance(mapping.id, UUID)
        assert mapping.google_event_id == "google-123"
        assert mapping.icloud_event_id == "icloud-456"
        assert mapping.content_hash == "hash123"
        assert mapping.created_at.tzinfo == pytz.UTC


class TestSyncResult:
    """Tests for SyncResult model."""
    
    def test_create_sync_result(self):
        """Test creating sync result."""
        result = SyncResult(
            operation=SyncOperation.CREATE,
            event_id="test-123",
            source=EventSource.GOOGLE,
            target=EventSource.ICLOUD,
            success=True,
            event_summary="Test Event"
        )
        
        assert result.operation == SyncOperation.CREATE
        assert result.event_id == "test-123"
        assert result.source == EventSource.GOOGLE
        assert result.target == EventSource.ICLOUD
        assert result.success
        assert result.event_summary == "Test Event"
        assert not result.conflict


class TestSyncReport:
    """Tests for SyncReport model."""
    
    def test_create_sync_report(self):
        """Test creating sync report."""
        report = SyncReport(dry_run=True)
        
        assert isinstance(report.sync_id, UUID)
        assert report.dry_run
        assert report.started_at.tzinfo == pytz.UTC
        assert report.total_operations == 0
        assert report.success_rate == 1.0
    
    def test_sync_report_with_results(self):
        """Test sync report with results."""
        report = SyncReport()
        
        # Add some results
        report.results = [
            SyncResult(
                operation=SyncOperation.CREATE,
                event_id="test-1",
                source=EventSource.GOOGLE,
                target=EventSource.ICLOUD,
                success=True
            ),
            SyncResult(
                operation=SyncOperation.UPDATE,
                event_id="test-2",
                source=EventSource.GOOGLE,
                target=EventSource.ICLOUD,
                success=False,
                error_message="Connection failed"
            ),
            SyncResult(
                operation=SyncOperation.CREATE,
                event_id="test-3",
                source=EventSource.ICLOUD,
                target=EventSource.GOOGLE,
                success=True
            )
        ]
        
        assert report.total_operations == 3
        assert report.success_rate == 2/3  # 2 successful out of 3
    
    def test_operation_counters(self):
        """Test operation counter properties."""
        report = SyncReport()
        
        # Set some counters
        report.google_to_icloud_created = 5
        report.google_to_icloud_updated = 3
        report.icloud_to_google_created = 2
        report.icloud_to_google_deleted = 1
        
        # Counters should be accessible
        assert report.google_to_icloud_created == 5
        assert report.google_to_icloud_updated == 3
        assert report.icloud_to_google_created == 2
        assert report.icloud_to_google_deleted == 1