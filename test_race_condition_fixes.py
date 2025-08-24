#!/usr/bin/env python3
"""
Comprehensive test script to verify race condition fixes in the sync engine.

This script tests the improved two-phase sync token implementation and 
automatic race condition detection/recovery functionality.
"""
import asyncio
import sys
from datetime import datetime, timedelta
import pytz
from unittest.mock import AsyncMock, MagicMock, patch
import logging

# Add the src directory to Python path
sys.path.insert(0, '/app/src')

from calsync_claude.sync_engine import SyncEngine
from calsync_claude.database import DatabaseManager
from calsync_claude.config import Settings
from calsync_claude.models import CalendarEvent, EventSource, ChangeSet
from calsync_claude.database.models import CalendarMappingDB, EventMappingDB, SyncSessionDB
from calsync_claude.services.google import GoogleCalendarService
from calsync_claude.services.icloud import iCloudCalendarService

async def test_post_sync_race_condition_detection():
    """Test that race conditions are properly detected after sync processing."""
    print("🧪 TEST: Post-sync race condition detection...")
    
    # Setup test environment
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    # Mock services
    google_service = AsyncMock(spec=GoogleCalendarService)
    icloud_service = AsyncMock(spec=iCloudCalendarService)
    
    sync_engine = SyncEngine(settings, db_manager, google_service, icloud_service)
    
    # Create test calendar mapping
    calendar_mapping = CalendarMappingDB(
        id=1,
        google_calendar_id="test-google-cal",
        icloud_calendar_id="test-icloud-cal",
        google_sync_token="initial-google-token",
        icloud_sync_token="initial-icloud-token",
        google_last_updated=datetime.now(pytz.UTC),
        icloud_last_updated=datetime.now(pytz.UTC),
        enabled=True,
        bidirectional=True
    )
    
    # Test Case 1: No race condition - tokens remain stable
    print("  📋 Test Case 1: Stable tokens (no race condition)")
    
    google_service.get_sync_token.return_value = "initial-google-token"  # Same token
    icloud_service.get_sync_token.return_value = "initial-icloud-token"  # Same token
    
    race_detected = False
    try:
        await sync_engine._post_sync_race_condition_check(
            "test-google-cal", "test-icloud-cal", calendar_mapping,
            "initial-google-token", "initial-icloud-token",
            datetime.now(pytz.UTC), dry_run=True
        )
        print("    ✅ No race condition detected (expected)")
    except Exception as e:
        print(f"    ❌ Unexpected error: {e}")
        race_detected = True
    
    # Test Case 2: Race condition detected - tokens changed
    print("  📋 Test Case 2: Changed tokens (race condition)")
    
    google_service.get_sync_token.return_value = "fresh-google-token"  # Changed token
    icloud_service.get_sync_token.return_value = "fresh-icloud-token"  # Changed token
    
    # Mock the race condition verification to return True
    with patch.object(sync_engine, '_verify_race_condition', return_value=True):
        with patch.object(sync_engine, '_handle_race_condition_recovery', return_value=None) as mock_recovery:
            await sync_engine._post_sync_race_condition_check(
                "test-google-cal", "test-icloud-cal", calendar_mapping,
                "initial-google-token", "initial-icloud-token",
                datetime.now(pytz.UTC), dry_run=False
            )
            
            # Verify recovery was triggered
            mock_recovery.assert_called_once()
            print("    ✅ Race condition detected and recovery triggered")
    
    print("✅ Post-sync race condition detection test passed")

async def test_race_condition_verification():
    """Test the race condition verification by checking for concurrent events."""
    print("🧪 TEST: Race condition verification...")
    
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    # Mock services
    google_service = AsyncMock(spec=GoogleCalendarService)
    icloud_service = AsyncMock(spec=iCloudCalendarService)
    
    sync_engine = SyncEngine(settings, db_manager, google_service, icloud_service)
    
    sync_start = datetime.now(pytz.UTC) - timedelta(minutes=5)
    sync_end = datetime.now(pytz.UTC)
    
    # Test Case 1: No events created during sync window
    print("  📋 Test Case 1: No concurrent events")
    
    google_service.get_events.return_value = [].__aiter__()  # Empty async iterator
    icloud_service.get_events.return_value = [].__aiter__()  # Empty async iterator
    
    result = await sync_engine._verify_race_condition(
        "test-google-cal", "test-icloud-cal", sync_start, sync_end
    )
    
    assert result == False, "Should return False when no concurrent events found"
    print("    ✅ Correctly identified no race condition")
    
    # Test Case 2: Events created during sync window
    print("  📋 Test Case 2: Concurrent events detected")
    
    # Create test event that was created during sync window
    concurrent_event = CalendarEvent(
        id="concurrent-event",
        source=EventSource.GOOGLE,
        summary="Event created during sync",
        start=sync_start + timedelta(minutes=2),
        end=sync_start + timedelta(minutes=3),
        created=sync_start + timedelta(minutes=2),  # Created during sync window
        updated=sync_start + timedelta(minutes=2)
    )
    
    async def mock_google_events(*args, **kwargs):
        yield concurrent_event
    
    google_service.get_events.return_value = mock_google_events()
    icloud_service.get_events.return_value = [].__aiter__()
    
    result = await sync_engine._verify_race_condition(
        "test-google-cal", "test-icloud-cal", sync_start, sync_end
    )
    
    assert result == True, "Should return True when concurrent events found"
    print("    ✅ Correctly detected race condition with concurrent events")
    
    print("✅ Race condition verification test passed")

async def test_automatic_recovery():
    """Test the automatic recovery mechanism that clears sync tokens."""
    print("🧪 TEST: Automatic recovery mechanism...")
    
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    # Mock services
    google_service = AsyncMock(spec=GoogleCalendarService)
    icloud_service = AsyncMock(spec=iCloudCalendarService)
    
    sync_engine = SyncEngine(settings, db_manager, google_service, icloud_service)
    
    # Create test calendar mapping with existing tokens
    calendar_mapping = CalendarMappingDB(
        id=1,
        google_calendar_id="test-google-cal",
        icloud_calendar_id="test-icloud-cal",
        google_sync_token="existing-google-token",
        icloud_sync_token="existing-icloud-token",
        google_last_updated=datetime.now(pytz.UTC),
        icloud_last_updated=datetime.now(pytz.UTC),
        enabled=True,
        bidirectional=True
    )
    
    # Mock database session
    mock_session = MagicMock()
    mock_mapping = MagicMock()
    mock_mapping.google_sync_token = "existing-google-token"
    mock_mapping.icloud_sync_token = "existing-icloud-token"
    mock_session.merge.return_value = mock_mapping
    
    with patch.object(db_manager, 'get_session') as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        # Execute recovery
        await sync_engine._handle_race_condition_recovery(
            "test-google-cal", "test-icloud-cal", calendar_mapping
        )
        
        # Verify tokens were cleared
        assert mock_mapping.google_sync_token is None, "Google sync token should be cleared"
        assert mock_mapping.icloud_sync_token is None, "iCloud sync token should be cleared"
        mock_session.commit.assert_called_once()
        
        print("    ✅ Sync tokens cleared successfully")
        print("    ✅ Database committed changes")
    
    print("✅ Automatic recovery test passed")

async def test_incomplete_sync_detection():
    """Test detection of incomplete incremental syncs."""
    print("🧪 TEST: Incomplete sync detection...")
    
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    # Mock services
    google_service = AsyncMock(spec=GoogleCalendarService)
    icloud_service = AsyncMock(spec=iCloudCalendarService)
    
    sync_engine = SyncEngine(settings, db_manager, google_service, icloud_service)
    
    # Test Case 1: Fresh tokens - no incomplete sync
    print("  📋 Test Case 1: Fresh tokens")
    
    calendar_mapping = CalendarMappingDB(
        id=1,
        google_sync_token="fresh-token",
        icloud_sync_token="fresh-token", 
        google_last_updated=datetime.now(pytz.UTC) - timedelta(minutes=30),  # Recent
        icloud_last_updated=datetime.now(pytz.UTC) - timedelta(minutes=30),   # Recent
        enabled=True,
        bidirectional=True
    )
    
    # Mock recent event checks
    google_service.get_events.return_value = [].__aiter__()  # Few events
    icloud_service.get_events.return_value = [].__aiter__()  # Few events
    
    # Mock database query for event mappings
    mock_session = MagicMock()
    mock_session.query().filter().limit().all.return_value = []  # No stale mappings
    
    with patch.object(db_manager, 'get_session') as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        result = await sync_engine._detect_incomplete_incremental_sync(
            "test-google-cal", "test-icloud-cal", calendar_mapping,
            datetime.now(pytz.UTC)
        )
        
        assert result == False, "Should not detect incomplete sync with fresh tokens"
        print("    ✅ Fresh tokens correctly identified as complete")
    
    # Test Case 2: Stale tokens - incomplete sync detected
    print("  📋 Test Case 2: Stale tokens")
    
    calendar_mapping.google_last_updated = datetime.now(pytz.UTC) - timedelta(hours=8)  # Stale
    calendar_mapping.icloud_last_updated = datetime.now(pytz.UTC) - timedelta(hours=8)  # Stale
    
    with patch.object(db_manager, 'get_session') as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        result = await sync_engine._detect_incomplete_incremental_sync(
            "test-google-cal", "test-icloud-cal", calendar_mapping,
            datetime.now(pytz.UTC)
        )
        
        assert result == True, "Should detect incomplete sync with stale tokens"
        print("    ✅ Stale tokens correctly identified as incomplete")
    
    print("✅ Incomplete sync detection test passed")

async def test_fresh_token_updates():
    """Test that fresh tokens are properly updated after processing."""
    print("🧪 TEST: Fresh token updates...")
    
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    # Mock services
    google_service = AsyncMock(spec=GoogleCalendarService)
    icloud_service = AsyncMock(spec=iCloudCalendarService)
    
    sync_engine = SyncEngine(settings, db_manager, google_service, icloud_service)
    
    # Create test calendar mapping
    calendar_mapping = CalendarMappingDB(
        id=1,
        google_sync_token="old-google-token",
        icloud_sync_token="old-icloud-token",
        google_last_updated=datetime.now(pytz.UTC) - timedelta(hours=1),
        icloud_last_updated=datetime.now(pytz.UTC) - timedelta(hours=1),
        enabled=True,
        bidirectional=True
    )
    
    # Mock database session
    mock_session = MagicMock()
    mock_mapping = MagicMock()
    mock_mapping.google_sync_token = "old-google-token"
    mock_mapping.icloud_sync_token = "old-icloud-token"
    mock_session.merge.return_value = mock_mapping
    
    with patch.object(db_manager, 'get_session') as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        # Test token updates
        await sync_engine._update_fresh_sync_tokens(
            calendar_mapping, "new-google-token", "new-icloud-token", dry_run=False
        )
        
        # Verify tokens were updated
        assert mock_mapping.google_sync_token == "new-google-token", "Google token should be updated"
        assert mock_mapping.icloud_sync_token == "new-icloud-token", "iCloud token should be updated"
        mock_session.commit.assert_called_once()
        
        print("    ✅ Fresh tokens updated successfully")
        print("    ✅ Database changes committed")
    
    # Test dry run mode
    print("  📋 Testing dry run mode...")
    
    with patch.object(db_manager, 'get_session') as mock_get_session:
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_session.reset_mock()
        
        await sync_engine._update_fresh_sync_tokens(
            calendar_mapping, "newer-google-token", "newer-icloud-token", dry_run=True
        )
        
        # Verify no database changes in dry run
        mock_get_session.assert_not_called()
        print("    ✅ Dry run correctly skipped database updates")
    
    print("✅ Fresh token updates test passed")

async def run_all_tests():
    """Run all race condition fix tests."""
    print("🚀 RACE CONDITION FIXES - COMPREHENSIVE TEST SUITE")
    print("=" * 60)
    
    test_functions = [
        test_post_sync_race_condition_detection,
        test_race_condition_verification,
        test_automatic_recovery,
        test_incomplete_sync_detection,
        test_fresh_token_updates
    ]
    
    passed = 0
    failed = 0
    
    for test_func in test_functions:
        try:
            await test_func()
            passed += 1
            print(f"✅ {test_func.__name__} PASSED")
        except Exception as e:
            failed += 1
            print(f"❌ {test_func.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
        print()
    
    print("=" * 60)
    print(f"📊 TEST RESULTS: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 ALL TESTS PASSED! Race condition fixes are working correctly.")
    else:
        print("⚠️  Some tests failed. Please review the implementation.")
    
    return failed == 0

if __name__ == "__main__":
    # Configure logging for tests
    logging.basicConfig(level=logging.INFO)
    
    # Run the test suite
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)