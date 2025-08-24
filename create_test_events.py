#!/usr/bin/env python3
"""
Script to create test events for verifying bidirectional sync.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
import pytz

# Add the src directory to Python path
sys.path.insert(0, '/app/src')

from calsync_claude.services.google import GoogleCalendarService
from calsync_claude.services.icloud import iCloudCalendarService
from calsync_claude.config import Settings
from calsync_claude.models import CalendarEvent, EventSource

async def create_test_events():
    """Create test events in both Google and iCloud calendars."""
    
    # Load settings
    settings = Settings()
    
    # Initialize services
    google_service = GoogleCalendarService(settings)
    icloud_service = iCloudCalendarService(settings)
    
    print("🔧 Initializing calendar services...")
    
    # Authenticate services
    print("🔐 Authenticating services...")
    await google_service.authenticate()
    await icloud_service.authenticate()
    print("✅ Services authenticated")
    
    # Get current time and create test events
    now = datetime.now(pytz.UTC)
    test_time = now + timedelta(hours=2)  # 2 hours from now
    
    # Create Google Calendar test event
    print("📅 Creating test event in Google Calendar...")
    google_event = CalendarEvent(
        id=f"test-google-{int(now.timestamp())}",
        uid=f"test-google-{int(now.timestamp())}",
        source=EventSource.GOOGLE,
        summary="🔍 Test Google Event - Two-Phase Sync",
        description="Test event created to verify bidirectional sync with two-phase token implementation",
        start=test_time,
        end=test_time + timedelta(hours=1),
        location="Virtual Test Location",
        all_day=False
    )
    
    try:
        # Get first available Google calendar
        google_calendars = await google_service.get_calendars()
        if not google_calendars:
            print("❌ No Google calendars found!")
            return
        
        # Use first available calendar (List[CalendarInfo])
        first_calendar = google_calendars[0]
        google_calendar_id = first_calendar.id
        calendar_name = first_calendar.name
        
        print(f"📍 Using Google calendar: {calendar_name} ({google_calendar_id})")
        
        created_google_event = await google_service.create_event(google_calendar_id, google_event)
        print(f"✅ Created Google event: {created_google_event.summary}")
        print(f"   Event ID: {created_google_event.id}")
        print(f"   UID: {created_google_event.uid}")
        
    except Exception as e:
        print(f"❌ Failed to create Google event: {e}")
        return
    
    # Wait a moment to ensure events are created at different times
    await asyncio.sleep(2)
    
    # Create iCloud Calendar test event
    print("\n📅 Creating test event in iCloud Calendar...")
    test_time_icloud = now + timedelta(hours=3)  # 3 hours from now
    icloud_event = CalendarEvent(
        id=f"test-icloud-{int(now.timestamp())}",
        uid=f"test-icloud-{int(now.timestamp())}",
        source=EventSource.ICLOUD,
        summary="🍎 Test iCloud Event - Two-Phase Sync", 
        description="Test event created to verify iCloud→Google sync with two-phase token implementation",
        start=test_time_icloud,
        end=test_time_icloud + timedelta(hours=1),
        location="Virtual Test Location iCloud",
        all_day=False
    )
    
    try:
        # Get first available iCloud calendar
        icloud_calendars = await icloud_service.get_calendars()
        if not icloud_calendars:
            print("❌ No iCloud calendars found!")
            return
        
        # Use first available calendar (List[CalendarInfo])
        first_calendar = icloud_calendars[0]
        icloud_calendar_id = first_calendar.id
        calendar_name = first_calendar.name
        
        print(f"📍 Using iCloud calendar: {calendar_name} ({icloud_calendar_id})")
        
        created_icloud_event = await icloud_service.create_event(icloud_calendar_id, icloud_event)
        print(f"✅ Created iCloud event: {created_icloud_event.summary}")
        print(f"   Event ID: {created_icloud_event.id}")
        print(f"   UID: {created_icloud_event.uid}")
        
    except Exception as e:
        print(f"❌ Failed to create iCloud event: {e}")
        return
    
    print(f"\n🎉 Successfully created test events!")
    print(f"📍 Google Event Time: {test_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"📍 iCloud Event Time: {test_time_icloud.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"\n⏱️  Now trigger a sync to test bidirectional synchronization:")
    print(f"   docker exec calsync-claude-dev calsync-claude sync")

if __name__ == "__main__":
    asyncio.run(create_test_events())