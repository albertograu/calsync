#!/usr/bin/env python3
"""
Script to debug sync operations and event mappings for our test events.
"""
import asyncio
import sys
from datetime import datetime, timedelta
import pytz

# Add the src directory to Python path
sys.path.insert(0, '/app/src')

from calsync_claude.database import DatabaseManager
from calsync_claude.config import Settings
from sqlalchemy import text

async def debug_sync_operations():
    """Debug sync operations and event mappings."""
    
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    print("🔍 Debugging sync operations for test events...")
    
    with db_manager.get_session() as session:
        # Look for sync operations related to our test events
        print("\n📊 Recent sync operations (last 20):")
        result = session.execute(text("""
            SELECT 
                operation, source, target, event_id, event_summary, success, error_message, timestamp
            FROM sync_operations 
            ORDER BY timestamp DESC 
            LIMIT 20
        """))
        
        operations = result.fetchall()
        if operations:
            for op in operations:
                operation, source, target, event_id, summary, success, error, timestamp = op
                status = "✅" if success else "❌"
                print(f"   {status} {timestamp}: {operation.upper()} {source}→{target}")
                print(f"      Event: '{summary}' (ID: {event_id})")
                if error:
                    print(f"      Error: {error}")
                print()
        else:
            print("   No sync operations found")
        
        # Look for event mappings related to our test events
        print("\n🔗 Event mappings for test events:")
        result = session.execute(text("""
            SELECT 
                google_event_id, icloud_event_id, google_ical_uid, icloud_uid, 
                event_uid, sync_direction, sync_status, last_sync_at
            FROM event_mappings 
            WHERE google_ical_uid LIKE '%test-%' 
               OR icloud_uid LIKE '%test-%'
               OR event_uid LIKE '%test-%'
            ORDER BY last_sync_at DESC
        """))
        
        mappings = result.fetchall()
        if mappings:
            for mapping in mappings:
                google_id, icloud_id, google_uid, icloud_uid, event_uid, direction, status, sync_time = mapping
                print(f"   📋 Mapping: {event_uid}")
                print(f"      Google ID: {google_id} (UID: {google_uid})")
                print(f"      iCloud ID: {icloud_id} (UID: {icloud_uid})")
                print(f"      Direction: {direction}, Status: {status}")
                print(f"      Last Sync: {sync_time}")
                print()
        else:
            print("   No event mappings found for test events")
            
        # Look for any operations mentioning "iCloud" in the summary
        print("\n🍎 Operations mentioning 'iCloud' or 'Test':")
        result = session.execute(text("""
            SELECT 
                operation, source, target, event_id, event_summary, success, error_message, timestamp
            FROM sync_operations 
            WHERE event_summary LIKE '%iCloud%' OR event_summary LIKE '%Test%'
            ORDER BY timestamp DESC 
            LIMIT 10
        """))
        
        test_operations = result.fetchall()
        if test_operations:
            for op in test_operations:
                operation, source, target, event_id, summary, success, error, timestamp = op
                status = "✅" if success else "❌"
                print(f"   {status} {timestamp}: {operation.upper()} {source}→{target}")
                print(f"      Event: '{summary}' (ID: {event_id})")
                if error:
                    print(f"      Error: {error}")
                print()
        else:
            print("   No operations found mentioning test events")
            
        # Check sync sessions to see if iCloud events are being detected
        print("\n📋 Recent sync sessions:")
        result = session.execute(text("""
            SELECT 
                started_at, status, 
                google_to_icloud_created, google_to_icloud_updated, google_to_icloud_skipped,
                icloud_to_google_created, icloud_to_google_updated, icloud_to_google_skipped
            FROM sync_sessions 
            ORDER BY started_at DESC 
            LIMIT 5
        """))
        
        sessions = result.fetchall()
        if sessions:
            for session_data in sessions:
                started, status, g_to_i_c, g_to_i_u, g_to_i_s, i_to_g_c, i_to_g_u, i_to_g_s = session_data
                print(f"   📅 {started} ({status})")
                print(f"      Google→iCloud: {g_to_i_c} created, {g_to_i_u} updated, {g_to_i_s} skipped")
                print(f"      iCloud→Google: {i_to_g_c} created, {i_to_g_u} updated, {i_to_g_s} skipped")
                print()
        else:
            print("   No sync sessions found")

if __name__ == "__main__":
    asyncio.run(debug_sync_operations())