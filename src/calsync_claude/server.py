import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse

from .config import Settings
from .sync_engine import SyncEngine


app = FastAPI(title="CalSync Server", version="2.0")


class SyncRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.trigger = asyncio.Event()
        self.running = True
        self.last_sync: Optional[datetime] = None
        self.sync_task: Optional[asyncio.Task] = None
        self.loop_interval_seconds = int(os.getenv("ICLOUD_POLL_SECONDS", "30"))
        # Google channel renewal
        self.enable_google_push = os.getenv("ENABLE_GOOGLE_PUSH", "false").lower() == "true"
        self.google_renew_interval_mins = int(os.getenv("GOOGLE_CHANNEL_RENEW_INTERVAL_MINS", "60"))
        self.google_renew_before_mins = int(os.getenv("GOOGLE_CHANNEL_RENEW_BEFORE_MINS", "1440"))
        self.google_channel_token = os.getenv("GOOGLE_CHANNEL_TOKEN")
        self.renew_task: Optional[asyncio.Task] = None

    async def run(self):
        while self.running:
            try:
                # Wait for either trigger or interval timeout
                try:
                    await asyncio.wait_for(self.trigger.wait(), timeout=self.loop_interval_seconds)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self.trigger.clear()

                # Run one sync
                async with SyncEngine(self.settings) as engine:
                    await engine.sync_calendars(dry_run=False)
                self.last_sync = datetime.utcnow()
            except Exception:
                # Avoid crash loop; log via print for container logs
                import traceback
                traceback.print_exc()
                await asyncio.sleep(2)

    def signal(self):
        if not self.trigger.is_set():
            self.trigger.set()


@app.on_event("startup")
async def on_startup():
    settings = Settings()
    app.state.settings = settings
    app.state.runtime = SyncRuntime(settings)
    app.state.runtime.sync_task = asyncio.create_task(app.state.runtime.run())
    # Start Google channel renewal loop
    app.state.runtime.renew_task = asyncio.create_task(_renew_loop(app.state.runtime))


@app.on_event("shutdown")
async def on_shutdown():
    runtime: SyncRuntime = app.state.runtime
    runtime.running = False
    runtime.signal()
    if runtime.sync_task:
        await asyncio.wait([runtime.sync_task], timeout=5)
    if runtime.renew_task:
        runtime.renew_task.cancel()
        try:
            await runtime.renew_task
        except asyncio.CancelledError:
            pass


@app.get("/health")
async def health():
    rt: SyncRuntime = app.state.runtime
    return {
        "ok": True,
        "last_sync": rt.last_sync.isoformat() if rt.last_sync else None,
        "interval_seconds": rt.loop_interval_seconds,
    }


@app.post("/webhooks/google")
async def google_webhook(request: Request):
    # Validate channel token if configured
    expected = os.getenv("GOOGLE_CHANNEL_TOKEN")
    token = request.headers.get("X-Goog-Channel-Token")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="invalid channel token")

    # Minimal header capture; we do not need body for sync trigger
    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_state = request.headers.get("X-Goog-Resource-State")
    resource_id = request.headers.get("X-Goog-Resource-ID")

    # Light validation
    if not channel_id or not resource_id:
        raise HTTPException(status_code=400, detail="missing channel/resource headers")

    # Trigger immediate sync
    app.state.runtime.signal()

    # Google expects 2xx quickly
    return Response(status_code=204)


async def _renew_loop(rt: SyncRuntime):
    """Periodically renew Google push channels (if enabled)."""
    while rt.running:
        try:
            if not rt.enable_google_push:
                await asyncio.sleep(rt.google_renew_interval_mins * 60)
                continue
            await _renew_google_channels(rt)
        except Exception:
            import traceback
            traceback.print_exc()
        await asyncio.sleep(rt.google_renew_interval_mins * 60)


async def _renew_google_channels(rt: SyncRuntime) -> None:
    """Renew channels in /data/google_channels.json that expire soon.

    Uses channel stop + events.watch to create a new channel for each calendar.
    """
    path = os.path.join(rt.settings.data_dir, 'google_channels.json')
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r') as f:
            items: List[Dict[str, Any]] = json.load(f) or []
    except Exception:
        return

    threshold = datetime.now(timezone.utc) + timedelta(minutes=rt.google_renew_before_mins)
    updated: List[Dict[str, Any]] = []

    # Run through SyncEngine to use authenticated Google service
    async with SyncEngine(rt.settings) as engine:
        svc = engine.google_service

        for ch in items:
            cal_id = ch.get('calendarId')
            address = ch.get('address')
            channel_id = ch.get('channelId') or ch.get('id')
            resource_id = ch.get('resourceId')
            expiration = ch.get('expiration')

            # Parse expiration
            exp_dt: Optional[datetime] = None
            if isinstance(expiration, str) and expiration.isdigit():
                try:
                    exp_dt = datetime.fromtimestamp(int(expiration)/1000, tz=timezone.utc)
                except Exception:
                    exp_dt = None
            elif isinstance(expiration, str):
                try:
                    exp_dt = datetime.fromisoformat(expiration)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    exp_dt = None

            needs_renew = (exp_dt is None) or (exp_dt <= threshold)
            if not needs_renew:
                updated.append(ch)
                continue

            # Stop old channel if possible
            if channel_id and resource_id:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: svc.service.channels().stop(
                            body={'id': channel_id, 'resourceId': resource_id}
                        ).execute()
                    )
                except Exception:
                    pass

            # Create new watch
            try:
                new_channel_id = str(uuid4())
                body = {'id': new_channel_id, 'type': 'web_hook', 'address': address}
                if rt.google_channel_token:
                    body['token'] = rt.google_channel_token
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: svc.service.events().watch(calendarId=cal_id, body=body).execute()
                )
                ch_new = {
                    'calendarId': cal_id,
                    'channelId': result.get('id', new_channel_id),
                    'resourceId': result.get('resourceId'),
                    'expiration': result.get('expiration'),
                    'address': address,
                }
                updated.append(ch_new)
            except Exception:
                # Keep old entry if renew fails; will retry next cycle
                updated.append(ch)

    # Write updated list back
    try:
        with open(path, 'w') as f:
            json.dump(updated, f, indent=2)
    except Exception:
        pass
