# CalSync Claude - Next Version TODO

## High Priority Fixes

### 🔴 Critical Issues
- [ ] **Fix SQLAlchemy session management issues** - Events failing with "Instance is not bound to a Session"
- [ ] **Fix status command calendar count** - Shows 0 calendars when mappings exist
- [ ] **Improve sync token acquisition** - Reduce "no sync tokens available" warnings

### 🟡 Feature Requests

#### Apple Calendar Protection
- [ ] **Implement iCloud event deletion protection** - Prevent deletion of Apple Calendar events
- [ ] **Add configuration option** - `ICLOUD_ALLOW_DELETIONS=false` to disable iCloud deletions
- [ ] **Update sync engine logic** - Allow create/update operations but skip delete operations for iCloud
- [ ] **Add deletion skip logging** - Log when iCloud deletions are skipped for transparency

**Implementation Plan:**
```python
# In sync_engine.py - modify iCloud sync operations
if operation_type == 'delete' and target_service == 'icloud':
    if not settings.icloud_allow_deletions:
        logger.info(f"Skipping iCloud event deletion (protection enabled): {event_id}")
        return  # Skip deletion
    
# Allow normal create/update operations to proceed
```

#### Configuration Changes
Add to `config.py`:
```python
# iCloud Calendar Protection
icloud_allow_deletions: bool = Field(
    default=False, 
    description="Allow deletion of iCloud calendar events"
)
```

Add to `docker-compose.yml`:
```yaml
environment:
  - ICLOUD_ALLOW_DELETIONS=${ICLOUD_ALLOW_DELETIONS:-false}
```

## Medium Priority Improvements

### 🔧 Data Cleanup
- [ ] **Clean up orphaned recurrence override events** - Remove events missing their master events
- [ ] **Database integrity check command** - `calsync-claude db check` to identify issues
- [ ] **Event mapping validation** - Ensure all mappings have valid UIDs and resource paths

### 📊 Monitoring & Diagnostics
- [ ] **Enhanced status reporting** - Show sync token health, recent errors
- [ ] **Event sync metrics** - Track success/failure rates by calendar
- [ ] **Session health monitoring** - Detect and alert on SQLAlchemy session issues

## Low Priority Enhancements

### 🚀 Performance
- [ ] **Batch event operations** - Process multiple events in single API calls
- [ ] **Smarter conflict resolution** - Use sequence numbers more effectively
- [ ] **Connection pooling** - Optimize database connections for async operations

### 🛠️ Developer Experience
- [ ] **Better error messages** - More descriptive CalendarServiceError details
- [ ] **Debug mode enhancements** - Detailed sync operation logging
- [ ] **Health check endpoint** - HTTP endpoint for monitoring systems

## Implementation Notes

### Apple Calendar Protection Logic
The key requirement is to make iCloud calendars **read-mostly**:
- ✅ **Allow**: Create new events from Google → iCloud
- ✅ **Allow**: Update existing iCloud events from Google changes  
- ❌ **Block**: Delete iCloud events (even if deleted from Google)

This protects against accidental data loss while maintaining sync functionality.

### Technical Considerations
1. **Bidirectional sync impact** - One-way deletions may cause sync conflicts
2. **User notification** - Log when deletions are skipped
3. **Override mechanism** - Allow temporary deletion enabling for cleanup
4. **Conflict handling** - How to handle events that exist only in iCloud

### Testing Strategy
- [ ] **Unit tests** for deletion protection logic
- [ ] **Integration tests** with Google→iCloud sync scenarios
- [ ] **Edge case testing** - Recurring events, all-day events, etc.
- [ ] **Configuration validation** - Ensure settings work correctly

## Current Status: ✅ WORKING

The sync system is now functional with:
- ✅ 4 active calendar mappings
- ✅ Event synchronization working
- ✅ Authentication resolved
- ⚠️ Minor session management issues (non-blocking)

Next version should focus on the Apple Calendar protection feature and session bug fixes.