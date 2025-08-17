import pytz
from datetime import datetime

from calsync_claude.config import Settings
from calsync_claude.sync_engine import SyncEngine
from calsync_claude.models import CalendarEvent, EventSource
from pydantic_settings import SettingsConfigDict


class TestSettings(Settings):
    """Test-specific settings that don't read from .env files."""
    model_config = SettingsConfigDict(
        env_file=None,  # Don't read from .env files
        case_sensitive=False,
        extra="ignore",
        secrets_dir=None  # Don't read from secrets directory
    )


def make_settings(tmp_path):
    return TestSettings(
        google_client_id='x'*20,
        google_client_secret='y'*20,
        icloud_username='user@example.com',
        icloud_password='abcd-efgh-ijkl-mnop',  # Valid app-specific password format
        database_url=f'sqlite:///{tmp_path}/test.db'
    )


def test_orphaned_override_clears_recurrence(tmp_path):
    settings = make_settings(tmp_path)
    engine = SyncEngine(settings)

    override = CalendarEvent(
        id='override1',
        uid='UID-1',
        source=EventSource.ICLOUD,
        summary='Orphaned Override',
        start=datetime(2023,1,1,tzinfo=pytz.UTC),
        end=datetime(2023,1,1,1,tzinfo=pytz.UTC),
        recurrence_overrides=[{'type':'recurrence-id','is_override':True,'master_event_id':'missing'}],
        recurring_event_id='missing'
    )

    grouped = engine._group_recurrence_events({'override1': override})
    standalone = grouped['override1']['master']
    assert standalone.recurrence_overrides == []
    assert getattr(standalone, 'recurring_event_id', None) is None
