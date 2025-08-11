# src/calsync_claude/config.py
"""Enhanced configuration management using Pydantic Settings."""

import os
from pathlib import Path
from typing import Optional, List, Set

from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ConflictResolution, SyncConfiguration, CalendarPair


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # New: allow reading credentials directly from files in this directory
        secrets_dir=os.getenv("SECRETS_DIR", "/run/secrets")
    )
    
    # Google Calendar API Configuration
    google_client_id: str = Field(..., description="Google OAuth Client ID")
    google_client_secret: str = Field(..., description="Google OAuth Client Secret")
    google_client_id_file: Optional[str] = Field(None, description="Path to file containing Google Client ID")
    google_client_secret_file: Optional[str] = Field(None, description="Path to file containing Google Client Secret")
    google_scopes: List[str] = Field(
        default=["https://www.googleapis.com/auth/calendar"],
        description="Google API scopes"
    )
    
    # iCloud Calendar Configuration (CalDAV)
    icloud_username: str = Field(..., description="iCloud username/email")
    icloud_password: str = Field(..., description="iCloud app-specific password")
    icloud_username_file: Optional[str] = Field(None, description="Path to file containing iCloud username")
    icloud_password_file: Optional[str] = Field(None, description="Path to file containing iCloud password")
    icloud_server_url: str = Field(
        default="https://caldav.icloud.com",
        description="iCloud CalDAV server URL"
    )
    
    # Application Configuration
    app_name: str = Field(default="calsync-claude", description="Application name")
    debug: bool = Field(default=False, description="Enable debug mode")
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string"
    )
    
    # Storage Configuration
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".calsync-claude",
        description="Application data directory"
    )
    database_url: str = Field(
        default="",
        description="Database URL (defaults to SQLite in data_dir)"
    )
    credentials_dir: Optional[Path] = Field(
        default=None,
        description="Credentials directory (defaults to data_dir/credentials)"
    )
    
    # Sync Configuration
    sync_config: SyncConfiguration = Field(
        default_factory=SyncConfiguration,
        description="Synchronization settings"
    )
    
    # Performance Configuration
    max_concurrent_requests: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum concurrent HTTP requests"
    )
    request_timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="HTTP request timeout"
    )
    rate_limit_requests_per_minute: int = Field(
        default=300,
        ge=1,
        description="Rate limit for API requests"
    )
    
    # Webhook Configuration
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Secret for webhook authentication"
    )
    webhook_ssl_cert: Optional[Path] = Field(
        default=None,
        description="SSL certificate for webhooks"
    )
    webhook_ssl_key: Optional[Path] = Field(
        default=None,
        description="SSL private key for webhooks"
    )
    
    @validator('data_dir', 'credentials_dir', pre=True)
    def expand_path(cls, v):
        """Expand user paths and convert to Path objects."""
        if v is None:
            return v
        if isinstance(v, str):
            return Path(v).expanduser().absolute()
        return v.expanduser().absolute()
    
    @validator('database_url')
    def set_default_database_url(cls, v, values):
        """Set default SQLite database URL if not provided."""
        if not v and 'data_dir' in values:
            data_dir = values['data_dir']
            return f"sqlite:///{data_dir}/calsync.db"
        return v
    
    @validator('credentials_dir')
    def set_default_credentials_dir(cls, v, values):
        """Set default credentials directory if not provided."""
        if v is None and 'data_dir' in values:
            return values['data_dir'] / "credentials"
        return v
    
    @validator('log_level')
    def validate_log_level(cls, v):
        """Validate log level."""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {valid_levels}")
        return v.upper()
    
    @validator('google_client_id')
    def validate_google_client_id(cls, v):
        """Validate Google OAuth Client ID format."""
        if not v:
            return v
        # Allow any non-empty string that looks like a reasonable client ID
        v = v.strip()
        if not v or len(v) < 10:
            raise ValueError("Google Client ID must be at least 10 characters")
        return v
    
    @validator('google_client_secret')
    def validate_google_client_secret(cls, v):
        """Validate Google Client Secret format."""
        if not v:
            return v
        # Allow any non-empty string that looks like a reasonable client secret
        v = v.strip()
        if not v or len(v) < 10:
            raise ValueError("Google Client Secret must be at least 10 characters")
        return v
    
    @validator('icloud_username')
    def validate_icloud_username(cls, v):
        """Validate iCloud username is a valid email."""
        if not v:
            return v
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, v):
            raise ValueError("iCloud username must be a valid email address")
        return v
    
    @validator('icloud_password')
    def validate_icloud_password(cls, v):
        """Validate iCloud app-specific password format."""
        if not v:
            return v
        # App-specific passwords are 16 characters, groups of 4 separated by dashes
        import re
        if not re.match(r'^[a-zA-Z]{4}-[a-zA-Z]{4}-[a-zA-Z]{4}-[a-zA-Z]{4}$', v):
            raise ValueError("iCloud password should be an app-specific password (format: xxxx-xxxx-xxxx-xxxx)")
        return v
    
    def __init__(self, **kwargs):
        """Initialize settings with file-based credential support."""
        # Load credentials from files if file paths are provided
        if 'google_client_id_file' in kwargs and kwargs['google_client_id_file']:
            kwargs['google_client_id'] = self._read_credential_file(kwargs['google_client_id_file'])
        if 'google_client_secret_file' in kwargs and kwargs['google_client_secret_file']:
            kwargs['google_client_secret'] = self._read_credential_file(kwargs['google_client_secret_file'])
        if 'icloud_username_file' in kwargs and kwargs['icloud_username_file']:
            kwargs['icloud_username'] = self._read_credential_file(kwargs['icloud_username_file'])
        if 'icloud_password_file' in kwargs and kwargs['icloud_password_file']:
            kwargs['icloud_password'] = self._read_credential_file(kwargs['icloud_password_file'])
        
        super().__init__(**kwargs)
    
    def _read_credential_file(self, file_path: str) -> str:
        """Read credential from file with proper error handling.
        
        Args:
            file_path: Path to credential file
            
        Returns:
            Credential value
            
        Raises:
            ValueError: If file cannot be read
        """
        try:
            with open(file_path, 'r') as f:
                credential = f.read().strip()
            if not credential:
                raise ValueError(f"Credential file {file_path} is empty")
            return credential
        except FileNotFoundError:
            raise ValueError(f"Credential file not found: {file_path}")
        except PermissionError:
            raise ValueError(f"Permission denied reading credential file: {file_path}")
        except Exception as e:
            raise ValueError(f"Error reading credential file {file_path}: {e}")
    
    def ensure_directories(self):
        """Create necessary directories with proper permissions."""
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)  # Owner only
        if self.credentials_dir:
            self.credentials_dir.mkdir(parents=True, exist_ok=True, mode=0o700)  # Owner only
    
    @property
    def google_credentials_path(self) -> Path:
        """Path to Google credentials file."""
        return self.credentials_dir / "google_credentials.json"
    
    @property
    def google_token_path(self) -> Path:
        """Path to Google OAuth token file."""
        return self.credentials_dir / "google_token.json"
    
    @property
    def sync_state_path(self) -> Path:
        """Path to sync state file (legacy support)."""
        return self.data_dir / "sync_state.json"
    
    def validate_required_settings(self) -> List[str]:
        """Validate required settings and return list of missing fields."""
        missing = []
        
        if not self.google_client_id:
            missing.append('GOOGLE_CLIENT_ID')
        if not self.google_client_secret:
            missing.append('GOOGLE_CLIENT_SECRET')
        if not self.icloud_username:
            missing.append('ICLOUD_USERNAME')
        if not self.icloud_password:
            missing.append('ICLOUD_PASSWORD')
        
        return missing


def load_settings(config_file: Optional[str] = None) -> Settings:
    """Load application settings.
    
    Args:
        config_file: Optional path to configuration file
        
    Returns:
        Settings instance
    """
    # Override default .env file if specified
    if config_file:
        os.environ.setdefault('SETTINGS_CONFIG_FILE', config_file)
    
    settings = Settings()
    settings.ensure_directories()
    return settings


def create_example_config(path: Path) -> None:
    """Create an example configuration file.
    
    Args:
        path: Path to create the example config file
    """
    example_content = '''# CalSync Claude Configuration
# Copy this file to .env and fill in your actual credentials

# Google Calendar API Configuration
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here

# iCloud Calendar Configuration (CalDAV)
ICLOUD_USERNAME=your_icloud_email@icloud.com
ICLOUD_PASSWORD=your_app_specific_password_here
ICLOUD_SERVER_URL=https://caldav.icloud.com

# Application Configuration
DEBUG=false
LOG_LEVEL=INFO

# Sync Configuration - these can be overridden via CLI
SYNC_CONFIG__SYNC_INTERVAL_MINUTES=30
SYNC_CONFIG__CONFLICT_RESOLUTION=manual
SYNC_CONFIG__MAX_EVENTS_PER_SYNC=1000
SYNC_CONFIG__SYNC_PAST_DAYS=30
SYNC_CONFIG__SYNC_FUTURE_DAYS=365
SYNC_CONFIG__RETRY_ATTEMPTS=3
SYNC_CONFIG__RETRY_DELAY_SECONDS=5
SYNC_CONFIG__ENABLE_WEBHOOKS=false
SYNC_CONFIG__WEBHOOK_PORT=8080

# Calendar Mappings - Define specific calendar synchronization pairs
# Format: JSON array of mapping objects
# Example: [{"google_calendar_id": "primary", "icloud_calendar_id": "Personal", "bidirectional": true}]
SYNC_CONFIG__CALENDAR_MAPPINGS=[]

# Auto-mapping settings
SYNC_CONFIG__AUTO_CREATE_CALENDARS=false
SYNC_CONFIG__CALENDAR_NAME_MAPPING={}

# Legacy support (will be converted to calendar_mappings automatically)
SYNC_CONFIG__SELECTED_GOOGLE_CALENDARS=[]
SYNC_CONFIG__SELECTED_ICLOUD_CALENDARS=[]

# Performance Configuration
MAX_CONCURRENT_REQUESTS=10
REQUEST_TIMEOUT_SECONDS=30
RATE_LIMIT_REQUESTS_PER_MINUTE=300

# Storage Configuration (optional)
# DATA_DIR=~/.calsync-claude
# DATABASE_URL=sqlite:///~/.calsync-claude/calsync.db

# Webhook Configuration (for real-time sync)
# WEBHOOK_SECRET=your_webhook_secret_here
# WEBHOOK_SSL_CERT=/path/to/cert.pem
# WEBHOOK_SSL_KEY=/path/to/key.pem
'''
    
    with open(path, 'w') as f:
        f.write(example_content)


def migrate_legacy_config_to_pairs(settings: Settings) -> List[CalendarPair]:
    """Migrate legacy configuration to explicit calendar pairs.
    
    Args:
        settings: Current settings instance
        
    Returns:
        List of CalendarPair instances created from legacy config
        
    Raises:
        ValueError: If legacy config cannot be migrated cleanly
    """
    pairs = []
    
    # If already has explicit pairs, return them
    if settings.sync_config.has_explicit_pairs():
        return settings.sync_config.get_active_pairs()
    
    # If has legacy mappings, convert them
    if settings.sync_config.calendar_mappings:
        for mapping in settings.sync_config.calendar_mappings:
            pair = mapping.to_calendar_pair()
            pairs.append(pair)
        return pairs
    
    # Convert legacy selected_* lists
    if (settings.sync_config.selected_google_calendars or 
        settings.sync_config.selected_icloud_calendars):
        
        google_cals = settings.sync_config.selected_google_calendars or ["primary"]
        icloud_cals = settings.sync_config.selected_icloud_calendars or []
        
        if len(google_cals) != len(icloud_cals):
            raise ValueError(
                f"Cannot migrate legacy config: {len(google_cals)} Google calendars "
                f"and {len(icloud_cals)} iCloud calendars. Cross-product sync is no longer "
                "supported. Please manually create explicit calendar_pairs with 1:1 relationships."
            )
        
        for i, (g_cal, i_cal) in enumerate(zip(google_cals, icloud_cals)):
            pair = CalendarPair(
                name=f"Migrated Pair {i+1}",
                google_calendar_id=g_cal,
                icloud_calendar_id=i_cal,
                bidirectional=True,
                enabled=True
            )
            pairs.append(pair)
    
    return pairs


def generate_pairs_config_example() -> str:
    """Generate an example configuration for calendar pairs."""
    return '''
# Example calendar pairs configuration
calendar_pairs = [
    {
        name = "Work Calendars"
        google_calendar_id = "your.work@gmail.com"
        icloud_calendar_id = "https://caldav.icloud.com/published/2/workCalendar"
        bidirectional = true
        enabled = true
    },
    {
        name = "Personal Calendars"  
        google_calendar_id = "primary"
        icloud_calendar_id = "https://caldav.icloud.com/published/2/personalCalendar"
        bidirectional = true
        enabled = true
    },
    {
        name = "One-way Sync (Google → iCloud)"
        google_calendar_id = "shared.calendar@gmail.com"
        icloud_calendar_id = "https://caldav.icloud.com/published/2/sharedCalendar"
        bidirectional = false
        sync_direction = "google_to_icloud"
        enabled = true
    }
]
'''