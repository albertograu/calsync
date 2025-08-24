#!/usr/bin/env python3
"""
Quick verification script to check if our race condition test events are syncing.
"""
import asyncio
import sys
from datetime import datetime, timedelta
import pytz

# Add the src directory to Python path
sys.path.insert(0, '/app/src')

from calsync_claude.services.google import GoogleCalendarService
from calsync_claude.services.icloud import iCloudCalendarService
from calsync_claude.config import Settings

async def verify_test_events():
    """Verify our race condition test events are present in both services."""
    
    settings = Settings()
    google_service = GoogleCalendarService(settings)
    icloud_service = iCloudCalendarService(settings)
    
    print("🔍 VERIFICATION: Checking for race condition test events...")
    
    # Authenticate
    await google_service.authenticate()
    await icloud_service.authenticate()
    
    # Search for our test events (timestamp: 1756049371)
    search_time = datetime(2025, 8, 24, 15, 0, tzinfo=pytz.UTC)
    search_end = search_time + timedelta(hours=6)
    
    print(f"🕒 Searching for events between {search_time} and {search_end}")
    
    # Find calendars
    google_calendars = await google_service.get_calendars()
    finances_google = None
    for cal in google_calendars:
        if "finances" in cal.name.lower():
            finances_google = cal
            break
    
    icloud_calendars = await icloud_service.get_calendars() 
    finances_icloud = None
    for cal in icloud_calendars:
        if "finances" in cal.name.lower():
            finances_icloud = cal
            break
    
    if not finances_google or not finances_icloud:
        print("❌ Could not find Finances calendars")
        return
    
    # Search Google calendar for our test events
    print("\n📧 Checking Google Calendar...")
    google_test_events = []
    try:
        async for event in google_service.get_events(
            finances_google.id,
            time_min=search_time,
            time_max=search_end,
            max_results=50
        ):
            if "1756049371" in event.summary or "Race Condition Fix Test" in event.summary:
                google_test_events.append(event)
                print(f"   ✅ Found: '{event.summary}' (UID: {event.uid})")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Search iCloud calendar for our test events
    print("\n🍎 Checking iCloud Calendar...")
    icloud_test_events = []
    try:
        async for event in icloud_service.get_events(
            finances_icloud.id,
            time_min=search_time,
            time_max=search_end,
            max_results=50
        ):
            if "1756049371" in event.summary or "Race Condition Fix Test" in event.summary:
                icloud_test_events.append(event)
                print(f"   ✅ Found: '{event.summary}' (UID: {event.uid})")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Analyze results
    print("\n📊 SYNC VERIFICATION RESULTS:")
    print(f"   Google events found: {len(google_test_events)}")
    print(f"   iCloud events found: {len(icloud_test_events)}")
    
    if len(google_test_events) >= 2 and len(icloud_test_events) >= 2:
        print("   🎉 SUCCESS: Both test events found in both calendars!")
        print("   🔄 Bidirectional sync is working correctly")
        
        # Check for proper event distribution
        google_uids = {e.uid for e in google_test_events}
        icloud_uids = {e.uid for e in icloud_test_events}
        
        if "race-fix-test-google-1756049371@calsync.local" in google_uids and \
           "race-fix-test-google-1756049371@calsync.local" in icloud_uids:
            print("   ✅ Google-originated event synced to iCloud")
        
        if "race-fix-test-icloud-1756049371@calsync.local" in google_uids and \
           "race-fix-test-icloud-1756049371@calsync.local" in icloud_uids:
            print("   ✅ iCloud-originated event synced to Google")
            
    elif len(google_test_events) == 1 and len(icloud_test_events) == 1:
        print("   ⚠️  PARTIAL: Only one test event found in each calendar")
        print("   🔄 Sync may still be in progress, check again in a few minutes")
    else:
        print("   ❌ ISSUE: Test events not properly synced")
        print("   📝 This may indicate the race condition fixes need more time")
    
    return len(google_test_events), len(icloud_test_events)

if __name__ == "__main__":
    asyncio.run(verify_test_events())