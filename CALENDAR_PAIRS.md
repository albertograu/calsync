# Calendar Pairs Configuration Guide

CalSync Claude v2.0+ uses explicit one-to-one calendar pairs instead of cross-product synchronization to prevent duplicates and improve performance.

## Key Changes

✅ **NEW**: Explicit calendar pairs  
❌ **REMOVED**: Cross-product sync between multiple calendars  
✅ **IMPROVED**: UID-based deduplication prevents duplicates  
✅ **IMPROVED**: Incremental sync for better performance  

## Configuration Format

### New Format (Recommended)

```toml
# Explicit calendar pairs - one Google calendar to one iCloud calendar
[[sync_config.calendar_pairs]]
name = "Work Calendars"
google_calendar_id = "your.work@gmail.com"
icloud_calendar_id = "https://caldav.icloud.com/published/2/MTIzNDU2Nzg5MA==/principal/calendars/work/"
bidirectional = true
enabled = true

[[sync_config.calendar_pairs]]
name = "Personal Calendars"  
google_calendar_id = "primary"  # Your main Google calendar
icloud_calendar_id = "https://caldav.icloud.com/published/2/MTIzNDU2Nzg5MA==/principal/calendars/personal/"
bidirectional = true
enabled = true

[[sync_config.calendar_pairs]]
name = "One-way Sync Example"
google_calendar_id = "shared.calendar@gmail.com"
icloud_calendar_id = "https://caldav.icloud.com/published/2/MTIzNDU2Nzg5MA==/principal/calendars/shared/"
bidirectional = false
sync_direction = "google_to_icloud"  # Only Google → iCloud
enabled = true
```

### Legacy Format (Deprecated)

```toml
# DEPRECATED: Will be removed in v3.0
# This old format only works if both lists have the same length (1:1 pairing)
sync_config.selected_google_calendars = ["primary", "work@gmail.com"]
sync_config.selected_icloud_calendars = ["icloud-personal-url", "icloud-work-url"]
```

## Migration Guide

### 1. Check Current Configuration

```bash
# Check what you have configured
calsync-claude pairs

# See example configuration format
calsync-claude pairs --example
```

### 2. Migrate Legacy Configuration

```bash
# Automatically migrate from legacy format
calsync-claude pairs --migrate
```

### 3. Validate Configuration

```bash
# Check for configuration errors
calsync-claude pairs --validate

# List all configured pairs
calsync-claude pairs --list
```

## Configuration Options

### CalendarPair Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Human-readable name for the pair |
| `google_calendar_id` | string | Yes | Google calendar ID (or "primary") |
| `icloud_calendar_id` | string | Yes | iCloud CalDAV URL |
| `bidirectional` | boolean | No | Enable two-way sync (default: true) |
| `sync_direction` | string | No | Direction if not bidirectional |
| `enabled` | boolean | No | Whether this pair is active (default: true) |
| `conflict_resolution` | string | No | Override global conflict resolution |

### Sync Directions

- `bidirectional: true` - Two-way sync (default)
- `sync_direction: "google_to_icloud"` - Only Google → iCloud  
- `sync_direction: "icloud_to_google"` - Only iCloud → Google

## Finding Calendar IDs

### Google Calendar ID
1. Go to Google Calendar settings
2. Click on the calendar name
3. Copy the "Calendar ID" (usually email@gmail.com or "primary")

### iCloud Calendar URL
1. The CLI will help you discover available calendars:
   ```bash
   calsync-claude test --detailed
   ```
2. Or check your iCloud calendar app and enable CalDAV sharing

## Benefits of Calendar Pairs

### ✅ Prevents Duplicates
- Each event syncs to exactly one target calendar
- UID-based deduplication prevents cross-contamination
- No more events appearing in multiple calendars

### ✅ Better Performance  
- Incremental sync only fetches changed events
- Reduced API calls and faster sync times
- Proper rate limiting and retry logic

### ✅ Clear Configuration
- Explicit relationships are easy to understand
- Validation prevents configuration errors
- Migration tools help transition from legacy setups

### ✅ Flexible Sync Patterns
- Bidirectional sync for most use cases
- One-way sync for read-only scenarios
- Per-pair conflict resolution policies

## Common Patterns

### Pattern 1: Mirror Setup
```toml
# Mirror your Google calendars to corresponding iCloud calendars
[[sync_config.calendar_pairs]]
name = "Personal Mirror"
google_calendar_id = "primary"
icloud_calendar_id = "personal-icloud-url"
bidirectional = true

[[sync_config.calendar_pairs]]
name = "Work Mirror"
google_calendar_id = "work@company.com"
icloud_calendar_id = "work-icloud-url" 
bidirectional = true
```

### Pattern 2: Backup Setup
```toml
# Backup Google calendars to iCloud (one-way)
[[sync_config.calendar_pairs]]
name = "Google Backup"
google_calendar_id = "primary"
icloud_calendar_id = "backup-icloud-url"
bidirectional = false
sync_direction = "google_to_icloud"
```

### Pattern 3: Aggregation Setup
```toml
# Collect multiple iCloud calendars into one Google calendar
[[sync_config.calendar_pairs]]
name = "Family → Google"
google_calendar_id = "family@gmail.com"
icloud_calendar_id = "family-member1-url"
bidirectional = false
sync_direction = "icloud_to_google"

[[sync_config.calendar_pairs]]
name = "Kids → Google"  
google_calendar_id = "family@gmail.com"
icloud_calendar_id = "kids-activities-url"
bidirectional = false
sync_direction = "icloud_to_google"
```

## Troubleshooting

### Configuration Errors

```bash
# Validate your configuration
calsync-claude pairs --validate

# Check for duplicate calendar usage
# Each calendar can only be used in one pair
```

### Migration Issues

If migration fails with cross-product error:
1. You had unequal numbers of Google/iCloud calendars configured
2. Cross-product sync is no longer supported
3. Manually create explicit pairs using `--example` as a guide

### Common Validation Errors

- **Duplicate calendar IDs**: Each calendar can only be used once
- **Invalid sync direction**: Must be "google_to_icloud" or "icloud_to_google" 
- **Missing sync direction**: Required when `bidirectional = false`

## Getting Help

```bash
# Show all available commands
calsync-claude --help

# Get help with pairs command
calsync-claude pairs --help

# Test your configuration
calsync-claude test

# Run a dry-run sync
calsync-claude sync --dry-run
```