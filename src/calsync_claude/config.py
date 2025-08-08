"""Enhanced configuration management using Pydantic Settings."""

import os
from pathlib import Path
from typing import Optional, List

from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ConflictResolution, SyncConfiguration


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # Google Calendar API Configuration
    google_client_id: str = Field(..., description="Google OAuth Client ID")
    google_client_secret: str = Field(..., description="Google OAuth Client Secret")
    google_scopes: List[str] = Field(
        default=["https://www.googleapis.com/auth/calendar"],
        description="Google API scopes"
    )
    
    # iCloud Calendar Configuration (CalDAV)
    icloud_username: str = Field(..., description="iCloud username/email")
    icloud_password: str = Field(..., description="iCloud app-specific password")
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
    
    def ensure_directories(self):
        """Create necessary directories."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.credentials_dir:
            self.credentials_dir.mkdir(parents=True, exist_ok=True)
    
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