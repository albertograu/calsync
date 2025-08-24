#!/usr/bin/env python3
"""
Script to clear sync tokens for the Finances calendar mapping to force full sync.
"""
import asyncio
import sys

# Add the src directory to Python path
sys.path.insert(0, '/app/src')

from calsync_claude.database import DatabaseManager
from calsync_claude.config import Settings
from sqlalchemy import text

async def clear_sync_tokens():
    """Clear sync tokens to force full sync and detect missing events."""
    
    settings = Settings()
    db_manager = DatabaseManager(settings)
    
    print("🧹 Clearing sync tokens for Finances calendar mapping...")
    
    with db_manager.get_session() as session:
        # Clear sync tokens for Finances calendar
        result = session.execute(text("""
            UPDATE calendar_mappings 
            SET 
                google_sync_token = NULL,
                icloud_sync_token = NULL
            WHERE icloud_calendar_id LIKE '%1A24FACA-CD89-42D8-B217-CC1D74761EC9%'
        """))
        
        affected_rows = result.rowcount
        session.commit()
        
        print(f"✅ Cleared sync tokens for {affected_rows} calendar mappings")
        print("🔄 Next sync will perform a full sync and detect all events")
        
        # Show updated mapping
        result = session.execute(text("""
            SELECT google_calendar_name, icloud_calendar_name, 
                   google_sync_token, icloud_sync_token
            FROM calendar_mappings 
            WHERE icloud_calendar_id LIKE '%1A24FACA-CD89-42D8-B217-CC1D74761EC9%'
        """))
        
        mapping = result.fetchone()
        if mapping:
            google_name, icloud_name, google_token, icloud_token = mapping
            print(f"📍 Updated mapping: {google_name} ↔ {icloud_name}")
            print(f"   Google sync token: {google_token or 'NULL (will force full sync)'}")
            print(f"   iCloud sync token: {icloud_token or 'NULL (will force full sync)'}")

if __name__ == "__main__":
    asyncio.run(clear_sync_tokens())