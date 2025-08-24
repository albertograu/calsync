#!/usr/bin/env python3
"""
Script to check if our test events are properly synced between Google and iCloud.
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

async def check_test_events():
    """Check for our test events in both services."""
    
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
    
    # Get time range for search (events created in last few hours)
    now = datetime.now(pytz.UTC)
    time_min = now - timedelta(hours=6)  # 6 hours ago
    time_max = now + timedelta(hours=6)  # 6 hours from now
    
    print(f"🔍 Searching for test events between {time_min} and {time_max}")
    
    # Check Google Calendar
    print("\n📅 Checking Google Calendar for test events...")
    try:
        google_calendars = await google_service.get_calendars()
        print(f"📍 Found {len(google_calendars)} Google calendars")
        
        google_test_events = []
        for calendar in google_calendars:
            print(f"   Searching in: {calendar.name} ({calendar.id})")
            
            # Get events from this calendar (async generator)
            events_found = 0
            async for event in google_service.get_events(
                calendar.id,
                time_min=time_min,
                time_max=time_max,
                max_results=100
            ):
                events_found += 1
                # Look for our test events
                if "Test" in event.summary and "Sync" in event.summary:
                    google_test_events.append({
                        'calendar': calendar.name,
                        'event': event,
                        'source': 'google'
                    })
                    print(f"   ✅ Found test event: '{event.summary}' (ID: {event.id}, UID: {event.uid})")
            
            print(f"   📊 Total events in {calendar.name}: {events_found}")
        
        if not google_test_events:
            print("   ❌ No test events found in Google Calendar")
            
    except Exception as e:
        print(f"   ❌ Error checking Google Calendar: {e}")
        google_test_events = []
    
    # Check iCloud Calendar
    print("\n🍎 Checking iCloud Calendar for test events...")
    try:
        icloud_calendars = await icloud_service.get_calendars()
        print(f"📍 Found {len(icloud_calendars)} iCloud calendars")
        
        icloud_test_events = []
        for calendar in icloud_calendars:
            print(f"   Searching in: {calendar.name} ({calendar.id})")
            
            # Get events from this calendar (async generator)
            events_found = 0
            async for event in icloud_service.get_events(
                calendar.id,
                time_min=time_min,
                time_max=time_max,
                max_results=100
            ):
                events_found += 1
                # Look for our test events
                if "Test" in event.summary and "Sync" in event.summary:
                    icloud_test_events.append({
                        'calendar': calendar.name,
                        'event': event,
                        'source': 'icloud'
                    })
                    print(f"   ✅ Found test event: '{event.summary}' (ID: {event.id}, UID: {event.uid})")
            
            print(f"   📊 Total events in {calendar.name}: {events_found}")
        
        if not icloud_test_events:
            print("   ❌ No test events found in iCloud Calendar")
            
    except Exception as e:
        print(f"   ❌ Error checking iCloud Calendar: {e}")
        icloud_test_events = []
    
    # Analyze sync status
    print("\n📊 Sync Analysis:")
    print(f"   Google test events found: {len(google_test_events)}")
    print(f"   iCloud test events found: {len(icloud_test_events)}")
    
    if google_test_events and icloud_test_events:
        print("   ✅ Events found in both services - bidirectional sync appears to be working!")
        
        # Check for cross-platform events
        google_uids = {event['event'].uid for event in google_test_events}
        icloud_uids = {event['event'].uid for event in icloud_test_events}
        
        common_uids = google_uids.intersection(icloud_uids)
        print(f"   📝 Common UIDs (synced events): {len(common_uids)}")
        for uid in common_uids:
            print(f"      - {uid}")
            
        google_only = google_uids - icloud_uids
        if google_only:
            print(f"   📊 Google-only UIDs (may not have synced to iCloud): {len(google_only)}")
            for uid in google_only:
                google_event = next((e['event'] for e in google_test_events if e['event'].uid == uid), None)
                if google_event:
                    print(f"      - {uid}: '{google_event.summary}'")
        
        icloud_only = icloud_uids - google_uids  
        if icloud_only:
            print(f"   📊 iCloud-only UIDs (may not have synced to Google): {len(icloud_only)}")
            for uid in icloud_only:
                icloud_event = next((e['event'] for e in icloud_test_events if e['event'].uid == uid), None)
                if icloud_event:
                    print(f"      - {uid}: '{icloud_event.summary}'")
                    
    elif google_test_events:
        print("   ⚠️  Events found only in Google - iCloud→Google sync may not be working")
    elif icloud_test_events:
        print("   ⚠️  Events found only in iCloud - Google→iCloud sync may not be working")
    else:
        print("   ❌ No test events found in either service")
    
    print("\n🔍 Detailed Event Information:")
    all_events = google_test_events + icloud_test_events
    for event_info in all_events:
        event = event_info['event']
        print(f"\n📅 {event_info['source'].upper()}: '{event.summary}'")
        print(f"   📍 Calendar: {event_info['calendar']}")
        print(f"   🆔 Event ID: {event.id}")
        print(f"   🏷️  UID: {event.uid}")
        print(f"   ⏰ Start: {event.start}")
        print(f"   📍 Location: {event.location}")

if __name__ == "__main__":
    asyncio.run(check_test_events())