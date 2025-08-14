# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Local Development (Primary workflow)
```bash
# First-time setup
./dev.sh setup          # Create .env and directories
nano .env               # Add Google/iCloud credentials  
./dev.sh auth           # Set up Google OAuth (opens browser)

# Daily development
./dev.sh run            # Start with live logs (recommended for testing)
./dev.sh daemon         # Start in background
./dev.sh logs           # Follow logs
./dev.sh rebuild        # Full rebuild after code changes
./dev.sh test           # One-time dry-run sync test
./dev.sh shell          # Debug inside container
./dev.sh status         # Check environment health
./dev.sh clean          # Clean up containers/images
```

### Production/VPS Deployment
```bash
./quick.sh build        # Build container
./quick.sh up           # Start containers
./quick.sh down         # Stop containers
./quick.sh restart      # Restart containers  
./quick.sh rebuild      # Full rebuild (uses rebuild.sh)
./quick.sh logs         # Follow logs
```

### Python Development (if working outside Docker)
```bash
# Install dependencies
pip install -e .[dev]

# Testing
pytest                                              # Run all tests
pytest tests/test_sync_engine.py                   # Run specific test
pytest --cov=src/calsync_claude --cov-report=html  # Run with coverage

# Code Quality
black src/ tests/       # Format code
ruff check src/ tests/  # Lint code  
mypy src/              # Type checking
```

## Project Architecture

### Core Components
- **`src/calsync_claude/cli.py`**: Rich CLI interface with Click commands
- **`src/calsync_claude/sync_engine.py`**: Main sync engine with async operations and conflict resolution
- **`src/calsync_claude/services/`**: Calendar service implementations
  - **`google.py`**: Google Calendar API service (OAuth 2.0)
  - **`icloud.py`**: iCloud CalDAV service  
  - **`base.py`**: Base calendar service interface
- **`src/calsync_claude/calendar_manager.py`**: Calendar discovery and mapping management
- **`src/calsync_claude/database.py`**: SQLite database with SQLAlchemy ORM
- **`src/calsync_claude/models.py`**: Pydantic data models and enums
- **`src/calsync_claude/config.py`**: Settings management with Pydantic Settings

### Database Schema
- **event_mappings**: Maps events between Google and iCloud calendars
- **sync_sessions**: Tracks sync runs and statistics  
- **sync_operations**: Individual sync operations
- **conflicts**: Unresolved conflicts requiring attention
- **config**: Persistent configuration storage

### Technology Stack
- **Async Python**: asyncio, httpx for non-blocking operations
- **Database**: SQLite with SQLAlchemy async ORM
- **CLI**: Rich for beautiful terminal interfaces, Click for commands
- **Logging**: structlog for structured logging
- **Validation**: Pydantic for data validation and settings
- **Retry Logic**: tenacity for exponential backoff
- **Calendar APIs**: google-api-python-client, caldav for iCloud

## Sync Engine Architecture

### Sync Process Flow
1. **Authentication**: Both Google (OAuth 2.0) and iCloud (CalDAV) services
2. **Calendar Discovery**: Detect available calendars and apply configured mappings
3. **Change Detection**: Use sync tokens and content hashing for incremental sync
4. **Conflict Resolution**: Apply configured strategy (latest_wins, google_wins, icloud_wins, manual)
5. **Bidirectional Sync**: Apply changes in both directions with proper error handling
6. **State Persistence**: Update database with new mappings and sync state

### Conflict Resolution Strategies
- **latest_wins**: Use event with most recent modification time
- **google_wins**: Always prefer Google Calendar version
- **icloud_wins**: Always prefer iCloud Calendar version  
- **manual**: Interactive resolution (not suitable for daemon mode)

### Key Features
- **Incremental Sync**: Uses sync tokens to only process changes since last sync
- **Retry Logic**: Automatic retries with exponential backoff for transient failures
- **Rate Limiting**: Respects API rate limits for both services
- **Deletion Handling**: Safe deletion processing with token validation
- **Data Integrity**: Content hashing for precise change detection

## Configuration Management

### Environment Variables (Primary)
```bash
# Google Calendar API
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# iCloud CalDAV  
ICLOUD_USERNAME=your_icloud_email@icloud.com
ICLOUD_PASSWORD=your_app_specific_password

# Application Settings
LOG_LEVEL=INFO|DEBUG
DEBUG=true|false
SYNC_INTERVAL_MINUTES=30
```

### Calendar Pairing Configuration
Calendar pairs are configured via `CALENDAR_PAIRS.md` or through the CLI:
```bash
calsync-claude calendars auto-map                    # Auto-map by name matching
calsync-claude calendars create-mapping --google "primary" --icloud "Personal"
calsync-claude calendars list                        # Show available calendars
calsync-claude calendars mappings                    # Show current mappings
```

## Development vs Production Settings

### Development Environment (`docker-compose.dev.yml`)
- Sync interval: 3 minutes (faster testing)
- Debug logging enabled
- Smaller batch sizes (100 events)
- Shorter time range (7 days past, 30 future)
- No auto-restart

### Production Environment (`docker-compose.yml`)  
- Sync interval: 30 minutes
- INFO level logging
- Full batch sizes (1000 events)
- Full time range (30 days past, 365 future)
- Auto-restart enabled

## Testing and Debugging

### Testing Workflow
1. Use `./dev.sh test` for safe dry-run testing
2. Use `./dev.sh run` for active development with live logs
3. Check `./dev.sh status` to verify environment setup
4. Use `./dev.sh shell` to debug inside container

### Common Debug Commands
```bash
# Inside container or local Python environment
calsync-claude sync --dry-run --verbose             # Test sync without changes
calsync-claude test                                 # Test calendar connections  
calsync-claude status                               # Check sync status
calsync-claude calendars list                       # Show available calendars
calsync-claude conflicts                            # View unresolved conflicts
```

## Authentication Setup

### Google Calendar (OAuth 2.0)
1. Create project in Google Cloud Console
2. Enable Google Calendar API
3. Create OAuth 2.0 credentials (Desktop application type)
4. Run `./dev.sh auth` to complete browser-based authentication
5. Token stored in `./credentials/google_token.json`

### iCloud Calendar (CalDAV)
1. Enable two-factor authentication on Apple ID
2. Generate app-specific password at https://appleid.apple.com/
3. Use app-specific password, NOT your main Apple ID password
4. Server URL: https://caldav.icloud.com

## Error Handling and Monitoring

### Common Error Patterns
- **Google 410 errors**: Invalid sync token, triggers safe backfill without deletions
- **iCloud 412 conflicts**: Concurrent modifications, handled by conflict resolution
- **Network timeouts**: Automatic retry with exponential backoff
- **Authentication failures**: Clear tokens and re-authenticate

### Monitoring
- Structured JSON logging in production
- Rich console output in development  
- Sync statistics and conflict tracking in database
- Health checks for calendar service connectivity

## Security Considerations

### Credential Storage
- OAuth tokens stored in `./credentials/` directory
- Use app-specific passwords for iCloud (never regular passwords)
- Mount credentials directory with appropriate permissions in Docker
- No credentials sent to third parties - local sync only

### Docker Security
- Credentials mounted as volumes, not in image
- Consider using Docker secrets for production deployment
- Protect file permissions on credential directories