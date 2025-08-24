#!/usr/bin/env python3
"""
Script to debug iCloud change detection specifically.
"""
import asyncio
import sys
from datetime import datetime, timedelta
import pytz

# Add the src directory to Python path
sys.path.insert(0, '/app/src')

from calsync_claude.services.icloud import iCloudCalendarService
from calsync_claude.config import Settings
from calsync_claude.database import DatabaseManager
from sqlalchemy import text

async def debug_icloud_changes():
    """Debug iCloud change detection for our test event."""
    
    settings = Settings()
    icloud_service = iCloudCalendarService(settings)
    db_manager = DatabaseManager(settings)
    
    print("🔍 Debugging iCloud change detection...")
    
    # Authenticate
    await icloud_service.authenticate()
    print("✅ iCloud service authenticated")
    
    # Get Finances calendar ID (where our test event exists)
    calendars = await icloud_service.get_calendars()
    finances_calendar = None
    for cal in calendars:
        if cal.name == "Finances":
            finances_calendar = cal
            break
    
    if not finances_calendar:
        print("❌ Finances calendar not found in iCloud")
        return
    
    print(f"📍 Using iCloud Finances calendar: {finances_calendar.id}")
    
    # Check current sync token from database
    with db_manager.get_session() as session:
        result = session.execute(text("""
            SELECT icloud_sync_token, icloud_last_updated
            FROM calendar_mappings 
            WHERE icloud_calendar_id = :calendar_id
        """), {"calendar_id": finances_calendar.id})
        
        mapping = result.fetchone()
        if mapping:
            current_sync_token, last_updated = mapping
            print(f"🔄 Current sync token: {current_sync_token}")
            print(f"⏰ Last updated: {last_updated}")
        else:
            print("❌ No calendar mapping found for Finances calendar")
            return
    
    # Try change detection with current sync token
    print("\n🔍 Testing change detection with current sync token...")
    try:
        change_set = await icloud_service.get_change_set(
            finances_calendar.id,
            sync_token=current_sync_token,
            max_results=100
        )
        
        print(f"📊 Change set results:")
        print(f"   Changed events: {len(change_set.changed)}")
        print(f"   Deleted events: {len(change_set.deleted_native_ids)}")
        print(f"   Next sync token: {change_set.next_sync_token}")
        print(f"   Invalid token used: {getattr(change_set, 'invalid_token_used', 'N/A')}")
        
        # Look for our test event in changes
        test_event_found = False
        for event_id, event in change_set.changed.items():
            if "Test iCloud Event" in event.summary:
                test_event_found = True
                print(f"   ✅ Found test event in changes: '{event.summary}' (ID: {event.id}, UID: {event.uid})")
        
        if not test_event_found:
            print("   ❌ Test iCloud event NOT found in change set")
            
    except Exception as e:
        print(f"   ❌ Error getting change set: {e}")
    
    # Try change detection WITHOUT sync token (full sync)
    print("\n🔍 Testing change detection WITHOUT sync token (full sync)...")
    try:
        now = datetime.now(pytz.UTC)
        time_min = now - timedelta(hours=12)
        time_max = now + timedelta(hours=12)
        
        change_set = await icloud_service.get_change_set(
            finances_calendar.id,
            sync_token=None,  # Force full sync
            time_min=time_min,
            time_max=time_max,
            max_results=100
        )
        
        print(f"📊 Full sync results:")
        print(f"   Changed events: {len(change_set.changed)}")
        print(f"   Deleted events: {len(change_set.deleted_native_ids)}")
        print(f"   Next sync token: {change_set.next_sync_token}")
        
        # Look for our test event in full sync
        test_event_found = False
        for event_id, event in change_set.changed.items():
            if "Test" in event.summary and ("iCloud" in event.summary or "Google" in event.summary):
                test_event_found = True
                print(f"   ✅ Found test event: '{event.summary}' (ID: {event.id}, UID: {event.uid})")
                print(f"      Created: {event.created}")
                print(f"      Updated: {event.updated}")
                print(f"      Start: {event.start}")
        
        if not test_event_found:
            print("   ❌ Test events NOT found in full sync")
            
    except Exception as e:
        print(f"   ❌ Error getting full sync: {e}")
    
    # Try direct event query to confirm our test event exists
    print("\n🔍 Directly querying for our test event...")
    try:
        events_found = 0
        test_events = []
        
        async for event in icloud_service.get_events(
            finances_calendar.id,
            time_min=now - timedelta(hours=12),
            time_max=now + timedelta(hours=12),
            max_results=100
        ):
            events_found += 1
            if "Test" in event.summary:
                test_events.append(event)
                
        print(f"📊 Direct query results:")
        print(f"   Total events found: {events_found}")
        print(f"   Test events found: {len(test_events)}")
        
        for event in test_events:
            print(f"   📅 '{event.summary}' (ID: {event.id}, UID: {event.uid})")
            print(f"      Created: {event.created}")
            print(f"      Updated: {event.updated}")
            print(f"      Start: {event.start}")
            
    except Exception as e:
        print(f"   ❌ Error in direct query: {e}")

if __name__ == "__main__":
    asyncio.run(debug_icloud_changes())