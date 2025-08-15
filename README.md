# CalSync vibe coded with Claude

An advanced two-way calendar synchronization tool that keeps your Google Calendar and iCloud Calendar perfectly in sync with enhanced conflict resolution, async operations, and comprehensive monitoring.

## 🚀 Key Features

### Core Functionality

- **Bidirectional Sync**: Real-time synchronization between Google Calendar and iCloud
- **Conflict Resolution**: Smart handling of conflicting changes with multiple strategies
- **Async Operations**: High-performance async I/O for faster synchronization
- **Incremental Sync**: Only processes changes since last sync for efficiency
- **Dry-Run Mode**: Preview changes before applying them

### Advanced Features

- **SQLite Database**: Persistent storage for sync state and event mappings
- **Rich CLI Interface**: Beautiful command-line interface with progress indicators
- **Structured Logging**: Comprehensive logging with structured output
- **Conflict Management**: Interactive conflict resolution interface
- **Daemon Mode**: Background synchronization with configurable intervals
- **Health Monitoring**: Connection testing and sync status reporting

### Reliability & Performance

- **Retry Logic**: Automatic retries with exponential backoff
- **Rate Limiting**: Respects API rate limits for both services
- **Error Recovery**: Graceful handling of network issues and API errors
- **Data Integrity**: Content hashing for precise change detection
- **Concurrent Operations**: Efficient parallel processing of calendar events

## 🔧 Installation

### Docker

This project ships with a Dockerfile and docker-compose for easy deployment on a VPS.

1. Prepare environment variables (recommended via `.env` alongside `docker-compose.yml`):

```
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
ICLOUD_USERNAME=your_icloud_email@icloud.com
ICLOUD_PASSWORD=your_app_specific_password
LOG_LEVEL=INFO
SYNC_INTERVAL_MINUTES=30
```

2. Build and run:

```
docker compose up -d --build
```

3. Volumes:

- `./data` stores the SQLite database and runtime state
- `./credentials` stores Google OAuth credentials/token files

4. One-off sync (instead of daemon):

```
docker compose run --rm calsync sync
```

Security notes:

- Use an app-specific password for iCloud and consider Docker/Kubernetes secrets or a password manager.
- The container writes credentials to `/credentials` (mounted to `./credentials`). Protect this path.

Token handling & deletions:

- Uses true incremental sync tokens on both sides. If Google returns 410 (invalid token), the app clears the token and does a safe backfill without processing deletions until a new token is minted.
- Deletions are processed only when valid sync tokens are present. iCloud deletions are detected via CalDAV sync-collection and mapped via stored resource URLs.

### Prerequisites

- Python 3.9 or higher
- Google Calendar API access
- iCloud account with app-specific password

### Install from Source

```bash
# Clone the repository
git clone https://github.com/your-username/calsync-claude.git
cd calsync-claude

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e .
```

### Install Dependencies

```bash
# Install all dependencies including development tools
pip install -e .[dev]

# Or install just the runtime dependencies
pip install -e .
```

## 🔑 Configuration

### 1. Create Configuration File

```bash
# Create example configuration
calsync-claude config create

# Or specify custom path
calsync-claude config create --path /path/to/config.env
```

### 2. Set Up Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the Google Calendar API
4. Create OAuth 2.0 Client ID credentials
5. Add the credentials to your configuration file

### 3. Set Up iCloud App-Specific Password

1. Go to [Apple ID account page](https://appleid.apple.com/)
2. Sign in with your Apple ID
3. Navigate to "Sign-In and Security" → "App-Specific Passwords"
4. Generate a new password for CalSync Claude
5. Add the credentials to your configuration file

### 4. Example Configuration

```env
# Google Calendar API
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here

# iCloud Calendar (CalDAV)
ICLOUD_USERNAME=your_icloud_email@icloud.com
ICLOUD_PASSWORD=your_app_specific_password_here
ICLOUD_SERVER_URL=https://caldav.icloud.com

# Application Settings
DEBUG=false
LOG_LEVEL=INFO

# Sync Configuration
SYNC_CONFIG__SYNC_INTERVAL_MINUTES=30
SYNC_CONFIG__CONFLICT_RESOLUTION=manual
SYNC_CONFIG__MAX_EVENTS_PER_SYNC=1000
SYNC_CONFIG__SYNC_PAST_DAYS=30
SYNC_CONFIG__SYNC_FUTURE_DAYS=365
```

## 🎯 Usage

### Basic Commands

```bash
# Test calendar connections
calsync-claude test

# Validate configuration
calsync-claude config validate

# One-time sync (dry run)
calsync-claude sync --dry-run

# Perform actual sync
calsync-claude sync

# Check sync status
calsync-claude status
```

### Multiple Calendar Management

```bash
# List all available calendars
calsync-claude calendars list

# Show current calendar mappings
calsync-claude calendars mappings

# Auto-map calendars based on name matching
calsync-claude calendars auto-map

# Create specific calendar mapping
calsync-claude calendars create-mapping --google "primary" --icloud "Personal"
calsync-claude calendars create-mapping --google "work@example.com" --icloud "Work" --bidirectional

# Create unidirectional mapping
calsync-claude calendars create-mapping --google "primary" --icloud "Shared" --unidirectional --direction google_to_icloud

# Delete calendar mapping
calsync-claude calendars delete-mapping <mapping-id>
```

### Conflict Resolution

```bash
# Sync with specific conflict resolution strategy
calsync-claude sync --conflict-resolution latest_wins
calsync-claude sync --conflict-resolution google_wins
calsync-claude sync --conflict-resolution icloud_wins

# View and manage conflicts
calsync-claude conflicts
```

### Daemon Mode

```bash
# Run as background daemon
calsync-claude daemon

# Custom sync interval
calsync-claude daemon --interval 15

# Dry-run daemon for testing
calsync-claude daemon --dry-run

# Limited number of runs
calsync-claude daemon --max-runs 10
```

### Advanced Options

```bash
# Use custom configuration file
calsync-claude --config /path/to/config.env sync

# Enable debug mode
calsync-claude --debug sync

# Verbose output
calsync-claude --verbose sync

# Reset all sync data
calsync-claude reset
```

## 🏗️ Architecture

### Modern Python Stack

- **Pydantic**: Data validation and settings management
- **SQLAlchemy**: Database ORM with async support
- **Rich**: Beautiful terminal interfaces
- **Structlog**: Structured logging
- **Tenacity**: Retry mechanisms
- **HTTPX**: Async HTTP client

### Component Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Rich CLI      │    │   Sync Engine    │    │   Database      │
│   Interface     │◄──►│   (Async Core)   │◄──►│   (SQLite)      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                    ┌───────────┼───────────┐
                    │                       │
            ┌───────▼────────┐    ┌────────▼────────┐
            │  Google Cal    │    │  iCloud Cal     │
            │   Service      │    │   Service       │
            │  (OAuth 2.0)   │    │  (CalDAV)       │
            └────────────────┘    └─────────────────┘
```

### Database Schema

- **event_mappings**: Maps events between Google and iCloud
- **sync_sessions**: Tracks sync runs and statistics
- **sync_operations**: Individual sync operations
- **conflicts**: Unresolved conflicts requiring attention
- **config**: Persistent configuration storage

## 🔄 Sync Process

1. **Authentication**: Authenticate with both Google and iCloud services
2. **Calendar Discovery**: Detect available calendars from both services
3. **Event Retrieval**: Fetch events within configured time range
4. **Change Detection**: Use content hashing to identify modifications
5. **Conflict Resolution**: Apply configured strategy for conflicts
6. **Bidirectional Sync**: Apply changes in both directions
7. **State Persistence**: Update database with new mappings and state
8. **Reporting**: Generate detailed sync report with statistics

## 🛠️ Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/your-username/calsync-claude.git
cd calsync-claude

# Install with development dependencies
pip install -e .[dev]

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/calsync_claude --cov-report=html

# Run specific test file
pytest tests/test_sync_engine.py
```

### Code Quality

```bash
# Format code
black src/ tests/

# Lint code
ruff check src/ tests/

# Type checking
mypy src/
```

### Database Migrations

```bash
# Generate migration
alembic revision --autogenerate -m "Add new feature"

# Apply migrations
alembic upgrade head
```

## 📊 Monitoring & Observability

### Structured Logging

- JSON formatted logs for production
- Human-readable logs for development
- Configurable log levels
- Request/response tracing

### Metrics & Statistics

- Sync success rates
- Operation counts (created/updated/deleted)
- Performance timing
- Error tracking
- Conflict statistics

### Health Checks

- Calendar service connectivity
- Database health
- Sync status reporting
- Recent activity summaries

## 🚨 Troubleshooting

### Common Issues

**Authentication Errors**

```bash
# Re-authenticate Google Calendar
rm ~/.calsync-claude/credentials/google_token.json
calsync-claude test
```

**iCloud Connection Issues**

- Verify app-specific password is used (not regular iCloud password)
- Ensure two-factor authentication is enabled
- Check iCloud server URL configuration

**Sync Conflicts**

```bash
# View conflicts
calsync-claude conflicts

# Reset sync data if needed
calsync-claude reset
```

**Database Issues**

```bash
# Reset database
calsync-claude reset

# Check sync status
calsync-claude status
```

### Debug Mode

```bash
# Enable debug logging
calsync-claude --debug sync

# Check configuration
calsync-claude config validate
```

## 🔐 Security

### Credential Storage

- OAuth tokens stored securely in user directory
- App-specific passwords (never regular passwords)
- No credentials sent to third parties
- Local-only synchronization

### Best Practices

- Use app-specific passwords for iCloud
- Regularly rotate API credentials
- Monitor sync logs for suspicious activity
- Keep software updated

## 📈 Comparison with Original

| Feature             | Original CalSync | CalSync Claude        |
| ------------------- | ---------------- | --------------------- |
| Architecture        | Synchronous      | Async/await           |
| Database            | JSON files       | SQLite with ORM       |
| CLI                 | Basic click      | Rich interface        |
| Conflict Resolution | Simple           | Advanced strategies   |
| Error Handling      | Basic            | Retry with backoff    |
| Logging             | Print statements | Structured logging    |
| Testing             | None             | Comprehensive suite   |
| Configuration       | Environment only | Pydantic settings     |
| Performance         | Sequential       | Concurrent operations |
| Monitoring          | Limited          | Comprehensive metrics |

## 📝 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and linting (`pytest && black . && ruff check .`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request
