#!/usr/bin/env python3
"""
Create verification test events to validate race condition fixes.

This script creates test events in both Google and iCloud calendars with timestamps
to verify that the race condition fixes are working and bidirectional sync is reliable.
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
from calsync_claude.models import CalendarEvent, EventSource

async def create_verification_test_events():
    """Create test events in both services to verify sync functionality."""
    
    # Load settings
    settings = Settings()
    
    # Initialize services
    google_service = GoogleCalendarService(settings)
    icloud_service = iCloudCalendarService(settings)
    
    print("🔧 BIDIRECTIONAL SYNC VERIFICATION - Creating Test Events")
    print("=" * 65)
    
    print("🔐 Authenticating calendar services...")
    await google_service.authenticate()
    await icloud_service.authenticate()
    print("✅ Services authenticated successfully")
    
    # Get current time for test events
    now = datetime.now(pytz.UTC)
    test_time = now + timedelta(hours=2)  # 2 hours from now
    timestamp = int(now.timestamp())
    
    print(f"📅 Test event scheduled for: {test_time}")
    print(f"🏷️  Timestamp identifier: {timestamp}")
    
    # Find calendars to use for testing
    print("\n🔍 Finding available calendars...")
    
    # Get Google calendars
    google_calendars = await google_service.get_calendars()
    google_calendar = None
    
    # Look for primary calendar or Finances calendar
    for cal in google_calendars:
        if cal.id == "primary" or "primary" in cal.id.lower():
            google_calendar = cal
            break
        elif "finances" in cal.name.lower():
            google_calendar = cal
            break
    
    if not google_calendar and google_calendars:
        google_calendar = google_calendars[0]  # Use first available
        
    if not google_calendar:
        print("❌ No Google calendars found")
        return
        
    print(f"📅 Using Google calendar: {google_calendar.name} ({google_calendar.id})")
    
    # Get iCloud calendars
    icloud_calendars = await icloud_service.get_calendars()
    icloud_calendar = None
    
    # Look for Finances calendar or Personal calendar
    for cal in icloud_calendars:
        if "finances" in cal.name.lower():
            icloud_calendar = cal
            break
        elif "personal" in cal.name.lower():
            icloud_calendar = cal
            break
    
    if not icloud_calendar and icloud_calendars:
        icloud_calendar = icloud_calendars[0]  # Use first available
        
    if not icloud_calendar:
        print("❌ No iCloud calendars found")
        return
        
    print(f"🍎 Using iCloud calendar: {icloud_calendar.name} ({icloud_calendar.id})")
    
    print("\n" + "=" * 65)
    
    # Create test event in Google Calendar
    print("📧 CREATING GOOGLE TEST EVENT...")
    
    try:
        google_event = CalendarEvent(
            id=f"race-fix-test-google-{timestamp}",
            uid=f"race-fix-test-google-{timestamp}@calsync.local",
            source=EventSource.GOOGLE,
            summary=f"🧪 Race Condition Fix Test - Google Event {timestamp}",
            description=(
                f"Test event created on {now.strftime('%Y-%m-%d %H:%M:%S UTC')} "
                f"to verify race condition fixes are working.\n\n"
                f"Expected behavior:\n"
                f"• This event should sync FROM Google TO iCloud\n"
                f"• The new race condition detection should prevent timing issues\n" 
                f"• Event should appear in iCloud calendar within next sync cycle\n\n"
                f"Test identifier: {timestamp}"
            ),
            start=test_time,
            end=test_time + timedelta(hours=1),
            location="Virtual Test Environment - Google Origin",
            all_day=False
        )
        
        created_google_event = await google_service.create_event(google_calendar.id, google_event)
        print(f"✅ Google test event created successfully!")
        print(f"   📋 Event ID: {created_google_event.id}")
        print(f"   🏷️  Event UID: {created_google_event.uid}")
        print(f"   📝 Summary: '{created_google_event.summary}'")
        print(f"   🕒 Start: {created_google_event.start}")
        print(f"   📍 Location: {created_google_event.location}")
        
    except Exception as e:
        print(f"❌ Failed to create Google test event: {type(e).__name__}: {e}")
        return
    
    print("\n" + "-" * 65)
    
    # Create test event in iCloud Calendar  
    print("🍎 CREATING ICLOUD TEST EVENT...")
    
    try:
        icloud_event = CalendarEvent(
            id=f"race-fix-test-icloud-{timestamp}",
            uid=f"race-fix-test-icloud-{timestamp}@calsync.local", 
            source=EventSource.ICLOUD,
            summary=f"🧪 Race Condition Fix Test - iCloud Event {timestamp}",
            description=(
                f"Test event created on {now.strftime('%Y-%m-%d %H:%M:%S UTC')} "
                f"to verify race condition fixes are working.\n\n"
                f"Expected behavior:\n"
                f"• This event should sync FROM iCloud TO Google\n"
                f"• The new post-processing token capture should prevent missed events\n"
                f"• Event should appear in Google calendar within next sync cycle\n\n" 
                f"Test identifier: {timestamp}"
            ),
            start=test_time + timedelta(minutes=30),  # 30 minutes after Google event
            end=test_time + timedelta(hours=1, minutes=30),
            location="Virtual Test Environment - iCloud Origin", 
            all_day=False
        )
        
        created_icloud_event = await icloud_service.create_event(icloud_calendar.id, icloud_event)
        print(f"✅ iCloud test event created successfully!")
        print(f"   📋 Event ID: {created_icloud_event.id}")
        print(f"   🏷️  Event UID: {created_icloud_event.uid}")
        print(f"   📝 Summary: '{created_icloud_event.summary}'")
        print(f"   🕒 Start: {created_icloud_event.start}")
        print(f"   📍 Location: {created_icloud_event.location}")
        
    except Exception as e:
        print(f"❌ Failed to create iCloud test event: {type(e).__name__}: {e}")
        return
    
    print("\n" + "=" * 65)
    print("🎉 TEST EVENTS CREATED SUCCESSFULLY!")
    print("=" * 65)
    
    print("\n📊 VERIFICATION CHECKLIST:")
    print("  ✅ Google test event created (should sync TO iCloud)")
    print("  ✅ iCloud test event created (should sync TO Google)")
    print("  📝 Both events have unique timestamps for identification")
    print("  🔍 Events scheduled 30 minutes apart to avoid conflicts")
    
    print("\n🔄 NEXT STEPS:")
    print("  1. Wait for next sync cycle to complete")
    print("  2. Check that Google event appears in iCloud calendar")
    print("  3. Check that iCloud event appears in Google calendar")
    print("  4. Monitor sync logs for race condition detection")
    print("  5. Verify no manual intervention is required")
    
    print("\n🕒 MONITORING TIMELINE:")
    print(f"  • Events created at: {now.strftime('%H:%M:%S UTC')}")
    print(f"  • Expected sync window: Within next 30 minutes")
    print(f"  • Check results after: {(now + timedelta(minutes=30)).strftime('%H:%M:%S UTC')}")
    
    print("\n📋 SUCCESS CRITERIA:")
    print("  🎯 Both test events appear in both calendar services")
    print("  🎯 No 'RACE CONDITION DETECTED' warnings in logs") 
    print("  🎯 No manual token clearing required")
    print("  🎯 Events maintain proper metadata and timing")
    
    print(f"\n🏷️  SEARCH IDENTIFIERS:")
    print(f"  • Timestamp: {timestamp}")
    print(f"  • Google UID: race-fix-test-google-{timestamp}@calsync.local")
    print(f"  • iCloud UID: race-fix-test-icloud-{timestamp}@calsync.local")
    
    return timestamp

async def main():
    """Create verification test events and provide monitoring guidance."""
    
    try:
        timestamp = await create_verification_test_events()
        
        if timestamp:
            print("\n🔍 MONITORING COMMANDS:")
            print(f"  # Check for events in logs:")
            print(f"  ./dev.sh logs | grep -i '{timestamp}'")
            print(f"  ./dev.sh logs | grep -i 'race condition'")
            print(f"  ./dev.sh logs | grep -i 'post-sync'")
            
            print(f"\n  # Check event sync status:")
            print(f"  ./dev.sh shell")
            print(f"  python check_test_events.py | grep -i '{timestamp}'")
        
    except Exception as e:
        print(f"❌ Error creating verification test events: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())