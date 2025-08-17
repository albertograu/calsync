import pytest
import pytz
from datetime import datetime, timedelta

from calsync_claude.config import Settings
from calsync_claude.sync_engine import SyncEngine
from calsync_claude.models import CalendarEvent, EventSource, SyncReport
from calsync_claude.database import CalendarMappingDB, EventMappingDB
from pydantic_settings import SettingsConfigDict


class TestSettings(Settings):
    """Test-specific settings that don't read from .env files."""
    model_config = SettingsConfigDict(
        env_file=None,  # Don't read from .env files
        case_sensitive=False,
        extra="ignore",
        secrets_dir=None  # Don't read from secrets directory
    )


class InMemoryService:
    def __init__(self, settings, source):
        self.settings = settings
        self.source = source
        self.events = {}

    async def authenticate(self):
        return None

    async def get_event(self, calendar_id, event_id):
        return self.events[event_id]

    async def create_event(self, calendar_id, event_data):
        self.events[event_data.id] = event_data
        return event_data

    async def update_event(self, calendar_id, event_id, event_data):
        self.events[event_id] = event_data
        return event_data

    async def delete_event(self, calendar_id, event_id):
        self.events.pop(event_id, None)
        return None


def make_settings(tmp_path):
    return TestSettings(
        google_client_id='x'*20,
        google_client_secret='y'*20,
        icloud_username='user@example.com',
        icloud_password='abcd-efgh-ijkl-mnop',  # Valid app-specific password format
        database_url=f'sqlite:///{tmp_path}/test.db'
    )


@pytest.fixture
def engine(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    engine = SyncEngine(settings)
    engine.db_manager.init_db()
    engine.google_service = InMemoryService(settings, EventSource.GOOGLE)
    engine.icloud_service = InMemoryService(settings, EventSource.ICLOUD)

    async def dummy_record(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "_record_sync_operation", dummy_record)
    return engine


def create_calendar_mapping(engine):
    with engine.db_manager.get_session() as session:
        mapping = CalendarMappingDB(
            google_calendar_id='g_cal',
            icloud_calendar_id='i_cal'
        )
        session.add(mapping)
        session.commit()
        return mapping


@pytest.mark.asyncio
async def test_event_creation_reflected(engine):
    calendar_mapping = create_calendar_mapping(engine)

    event = CalendarEvent(
        id='evt1',
        source=EventSource.GOOGLE,
        summary='Create Test',
        start=datetime.now(pytz.UTC),
        end=datetime.now(pytz.UTC) + timedelta(hours=1)
    )

    report = SyncReport()
    await engine._sync_event_to_target(
        event,
        EventSource.ICLOUD,
        'i_cal',
        calendar_mapping,
        {},
        None,
        report,
        dry_run=False
    )

    assert 'evt1' in engine.icloud_service.events
    with engine.db_manager.get_session() as session:
        mapping = session.query(EventMappingDB).first()
        assert mapping.google_event_id == 'evt1'
        assert mapping.icloud_event_id == 'evt1'


@pytest.mark.asyncio
async def test_event_update_reflected(engine):
    calendar_mapping = create_calendar_mapping(engine)

    original = CalendarEvent(
        id='evt2',
        source=EventSource.GOOGLE,
        summary='Original',
        start=datetime.now(pytz.UTC),
        end=datetime.now(pytz.UTC) + timedelta(hours=1)
    )
    engine.google_service.events['evt2'] = original
    engine.icloud_service.events['evt2'] = original
    content_hash = original.content_hash()

    with engine.db_manager.get_session() as session:
        mapping = engine.db_manager.create_event_mapping(
            session,
            google_event_id='evt2',
            icloud_event_id='evt2',
            google_calendar_id='g_cal',
            icloud_calendar_id='i_cal',
            content_hash=content_hash,
            calendar_mapping_id=calendar_mapping.id,
            sync_direction='google_to_icloud'
        )

    updated = original.copy(update={'summary': 'Updated'})

    report = SyncReport()
    await engine._sync_event_to_target(
        updated,
        EventSource.ICLOUD,
        'i_cal',
        calendar_mapping,
        {'evt2': mapping},
        None,
        report,
        dry_run=False
    )

    assert engine.icloud_service.events['evt2'].summary == 'Updated'


@pytest.mark.asyncio
async def test_event_deletion_reflected(engine):
    calendar_mapping = create_calendar_mapping(engine)
    calendar_mapping.google_sync_token = 'token'

    original = CalendarEvent(
        id='evt3',
        source=EventSource.GOOGLE,
        summary='To Delete',
        start=datetime.now(pytz.UTC),
        end=datetime.now(pytz.UTC) + timedelta(hours=1)
    )
    engine.google_service.events['evt3'] = original
    engine.icloud_service.events['evt3'] = original

    with engine.db_manager.get_session() as session:
        mapping = engine.db_manager.create_event_mapping(
            session,
            google_event_id='evt3',
            icloud_event_id='evt3',
            google_calendar_id='g_cal',
            icloud_calendar_id='i_cal',
            content_hash=original.content_hash(),
            calendar_mapping_id=calendar_mapping.id,
            sync_direction='google_to_icloud'
        )

    report = SyncReport()
    await engine._handle_deletions(
        {'evt3'},
        set(),
        [mapping],
        'g_cal',
        'i_cal',
        calendar_mapping,
        None,
        report,
        dry_run=False
    )

    assert 'evt3' not in engine.icloud_service.events


@pytest.mark.asyncio
async def test_address_and_notes_change(engine):
    calendar_mapping = create_calendar_mapping(engine)

    original = CalendarEvent(
        id='evt4',
        source=EventSource.GOOGLE,
        summary='Address Notes',
        location='Old Place',
        description='Old notes',
        start=datetime.now(pytz.UTC),
        end=datetime.now(pytz.UTC) + timedelta(hours=1)
    )
    engine.google_service.events['evt4'] = original
    engine.icloud_service.events['evt4'] = original
    with engine.db_manager.get_session() as session:
        mapping = engine.db_manager.create_event_mapping(
            session,
            google_event_id='evt4',
            icloud_event_id='evt4',
            google_calendar_id='g_cal',
            icloud_calendar_id='i_cal',
            content_hash=original.content_hash(),
            calendar_mapping_id=calendar_mapping.id,
            sync_direction='google_to_icloud'
        )

    updated = original.copy(update={'location': 'New Place', 'description': 'New notes'})

    report = SyncReport()
    await engine._sync_event_to_target(
        updated,
        EventSource.ICLOUD,
        'i_cal',
        calendar_mapping,
        {'evt4': mapping},
        None,
        report,
        dry_run=False
    )

    updated_evt = engine.icloud_service.events['evt4']
    assert updated_evt.location == 'New Place'
    assert updated_evt.description == 'New notes'
