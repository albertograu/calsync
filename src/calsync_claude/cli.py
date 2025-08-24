"""Enhanced command-line interface with Rich formatting and advanced features."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Set

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.tree import Tree
import structlog

from .config import Settings, load_settings, create_example_config, migrate_legacy_config_to_pairs, generate_pairs_config_example
from .sync_engine import SyncEngine
from .models import ConflictResolution, CalendarPair
from .database import DatabaseManager
from uuid import uuid4

console = Console()
logger = structlog.get_logger()


def setup_logging(level: str, debug: bool = False) -> None:
    """Set up structured logging."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def async_command(f):
    """Decorator to wrap async click commands."""
    @click.pass_context
    def wrapper(ctx, *args, **kwargs):
        return asyncio.run(f(ctx, *args, **kwargs))
    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


@click.group()
@click.version_option(version="2.0.0")
@click.option('--config', '-c', type=click.Path(exists=True), 
              help='Path to configuration file')
@click.option('--debug', is_flag=True, help='Enable debug mode')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.pass_context
def cli(ctx, config, debug, verbose):
    """CalSync Claude - Advanced two-way calendar synchronization.
    
    Now uses explicit one-to-one calendar pairs (no more cross-product sync).
    Use 'calsync-claude pairs' to configure and manage your calendar pairings.
    
    Features: UID-based deduplication, incremental sync, proper timezone handling,
    sequence-based conflict resolution, and rate limiting with retries.
    """
    ctx.ensure_object(dict)
    
    try:
        # Load settings
        settings = load_settings(config)
        if debug:
            settings.debug = True
        if verbose:
            settings.log_level = 'DEBUG'
        
        ctx.obj['settings'] = settings
        
        # Setup logging
        setup_logging(settings.log_level, settings.debug)
        
    except Exception as e:
        console.print(f"[red]Error loading configuration: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option('--host', default='0.0.0.0', help='Bind host for HTTP server')
@click.option('--port', default=8080, type=int, help='Bind port for HTTP server')
def serve(host, port):
    """Run HTTP server with background sync daemon (container friendly)."""
    try:
        import uvicorn
        uvicorn.run("calsync_claude.server:app", host=host, port=port, reload=False)
    except Exception as e:
        console.print(f"[red]Failed to start server: {e}[/red]")
        sys.exit(1)

@cli.command()
@click.option('--dry-run', '-n', is_flag=True, 
              help='Show what would be synced without making changes')
@click.option('--conflict-resolution', '-r',
              type=click.Choice(['manual', 'latest_wins', 'google_wins', 'icloud_wins']),
              help='Conflict resolution strategy')
@async_command
async def sync(ctx, dry_run, conflict_resolution):
    """Synchronize calendars between Google and iCloud."""
    settings = ctx.obj['settings']
    
    # Override conflict resolution if specified
    if conflict_resolution:
        settings.sync_config.conflict_resolution = ConflictResolution(conflict_resolution)
    
    # Validate configuration
    missing_fields = settings.validate_required_settings()
    if missing_fields:
        console.print(Panel(
            f"[red]Missing required configuration fields:[/red]\n" +
            "\n".join(f"• {field}" for field in missing_fields) +
            f"\n\nPlease set these environment variables or create a configuration file.\n" +
            f"Use [bold]calsync-claude config create[/bold] to create an example file.",
            title="Configuration Error"
        ))
        sys.exit(1)
    
    if dry_run:
        console.print("[yellow]Running in dry-run mode - no changes will be made[/yellow]")
    
    try:
        # Initialize sync engine
        console.print("🔧 Initializing sync engine...")
        
        async with SyncEngine(settings) as sync_engine:
            console.print("🔧 Testing connections...")
            
            # Test connections
            connection_results = await sync_engine.test_connections()
            
            if not connection_results['google']['success']:
                console.print(f"[red]Google Calendar connection failed: {connection_results['google']['error']}[/red]")
                sys.exit(1)
            
            if not connection_results['icloud']['success']:
                console.print(f"[red]iCloud Calendar connection failed: {connection_results['icloud']['error']}[/red]")
                sys.exit(1)
            
            console.print("🚀 Synchronizing calendars...")
            
            # Perform sync
            sync_report = await sync_engine.sync_calendars(dry_run=dry_run)
            
            console.print("✅ Sync completed")
        
        # Display results
        _display_sync_results(sync_report)
        
        # Show conflicts if any
        if sync_report.conflicts:
            _display_conflicts(sync_report.conflicts)
        
        # Show errors if any
        if sync_report.errors:
            console.print(Panel(
                "\n".join(f"• {error}" for error in sync_report.errors),
                title="[red]Errors[/red]",
                border_style="red"
            ))
    
    except KeyboardInterrupt:
        console.print("[yellow]Sync cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        if settings.debug:
            console.print_exception()
        sys.exit(1)


@cli.command()
@async_command
async def status(ctx):
    """Show sync status and recent activity."""
    settings = ctx.obj['settings']
    
    try:
        async with SyncEngine(settings) as sync_engine:
            # Test connections
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Checking status...", total=None)
                
                connection_results = await sync_engine.test_connections()
                sync_status = await sync_engine.get_sync_status()
        
        # Display connection status
        _display_connection_status(connection_results)
        
        # Display sync statistics
        _display_sync_status(sync_status)
        
    except Exception as e:
        console.print(f"[red]Failed to get status: {e}[/red]")
        if settings.debug:
            console.print_exception()
        sys.exit(1)


@cli.command()
@async_command
async def test(ctx):
    """Test calendar connections and display sample events."""
    settings = ctx.obj['settings']
    
    # Validate configuration
    missing_fields = settings.validate_required_settings()
    if missing_fields:
        console.print(Panel(
            f"[red]Missing required configuration fields:[/red]\n" +
            "\n".join(f"• {field}" for field in missing_fields),
            title="Configuration Error"
        ))
        sys.exit(1)
    
    try:
        async with SyncEngine(settings) as sync_engine:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Testing connections...", total=None)
                
                connection_results = await sync_engine.test_connections()
                
                progress.update(task, description="Getting sample data...")
                
                # Get sample events
                try:
                    google_calendars = await sync_engine.google_service.get_calendars()
                    icloud_calendars = await sync_engine.icloud_service.get_calendars()
                except Exception as e:
                    console.print(f"[red]Failed to get calendars: {e}[/red]")
                    return
        
        # Display detailed test results
        _display_test_results(connection_results, google_calendars, icloud_calendars)
        
    except Exception as e:
        console.print(f"[red]Connection test failed: {e}[/red]")
        if settings.debug:
            console.print_exception()
        sys.exit(1)


@cli.command()
@click.option('--interval', '-i', type=int, 
              help='Sync interval in minutes (overrides config)')
@click.option('--dry-run', '-n', is_flag=True, 
              help='Run in dry-run mode')
@click.option('--max-runs', type=int,
              help='Maximum number of sync runs (default: infinite)')
@async_command
async def daemon(ctx, interval, dry_run, max_runs):
    """Run CalSync continuously as a daemon."""
    settings = ctx.obj['settings']
    
    if interval:
        settings.sync_config.sync_interval_minutes = interval
    
    sync_interval = settings.sync_config.sync_interval_minutes
    
    if dry_run:
        console.print("[yellow]Running daemon in dry-run mode[/yellow]")
    
    console.print(f"[green]Starting CalSync daemon[/green] - interval: {sync_interval} minutes")
    
    runs = 0
    try:
        while True:
            if max_runs and runs >= max_runs:
                console.print(f"[yellow]Reached maximum runs ({max_runs}), stopping daemon[/yellow]")
                break
            
            console.print(f"\n[blue]--- Sync Run {runs + 1} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---[/blue]")
            
            try:
                async with SyncEngine(settings) as sync_engine:
                    sync_report = await sync_engine.sync_calendars(dry_run=dry_run)
                    _display_sync_results(sync_report, compact=True)
                    
                    if sync_report.conflicts:
                        console.print(f"[yellow]⚠️  {len(sync_report.conflicts)} conflicts detected[/yellow]")
                    
                    if sync_report.errors:
                        console.print(f"[red]❌ {len(sync_report.errors)} errors occurred[/red]")
                        for error in sync_report.errors:
                            console.print(f"   {error}")
                
                runs += 1
                
            except Exception as e:
                console.print(f"[red]Sync run failed: {e}[/red]")
                if settings.debug:
                    console.print_exception()
            
            # Wait for next run
            if max_runs and runs >= max_runs:
                break
            
            console.print(f"[dim]Next sync in {sync_interval} minutes...[/dim]")
            await asyncio.sleep(sync_interval * 60)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped by user[/yellow]")
        sys.exit(0)


@cli.group()
def config():
    """Configuration management commands."""
    pass


@config.command('create')
@click.option('--path', '-p', type=click.Path(), default='.env',
              help='Path to create config file')
@click.option('--force', '-f', is_flag=True,
              help='Overwrite existing file')
def create_config(path, force):
    """Create an example configuration file."""
    config_path = Path(path)
    
    if config_path.exists() and not force:
        if not Confirm.ask(f"File {path} already exists. Overwrite?"):
            console.print("[yellow]Configuration creation cancelled[/yellow]")
            return
    
    try:
        create_example_config(config_path)
        console.print(f"[green]Configuration file created at {path}[/green]")
        console.print("Please edit the file with your actual credentials.")
    except Exception as e:
        console.print(f"[red]Failed to create configuration file: {e}[/red]")


@config.command('validate')
@click.pass_context
def validate_config(ctx):
    """Validate the current configuration."""
    settings = ctx.obj['settings']
    
    missing_fields = settings.validate_required_settings()
    
    if missing_fields:
        console.print(Panel(
            f"[red]Missing required fields:[/red]\n" +
            "\n".join(f"• {field}" for field in missing_fields),
            title="Configuration Validation",
            border_style="red"
        ))
        sys.exit(1)
    else:
        console.print(Panel(
            "[green]✓ All required configuration fields are present[/green]",
            title="Configuration Validation",
            border_style="green"
        ))


@cli.command()
@click.confirmation_option(prompt='Are you sure you want to reset all sync data?')
@click.pass_context
def reset(ctx):
    """Reset all synchronization data and mappings."""
    settings = ctx.obj['settings']
    
    try:
        db_manager = DatabaseManager(settings)
        
        # Drop and recreate all tables
        from .database import Base
        Base.metadata.drop_all(bind=db_manager.engine)
        Base.metadata.create_all(bind=db_manager.engine)
        
        console.print("[green]✓ All sync data has been reset[/green]")
        console.print("[yellow]⚠️  Next sync will treat all events as new[/yellow]")
        
    except Exception as e:
        console.print(f"[red]Failed to reset sync data: {e}[/red]")
        sys.exit(1)


@cli.group()
def calendars():
    """Calendar management commands."""
    pass


@calendars.command('list')
@async_command
async def list_calendars(ctx):
    """List all available calendars from both services."""
    settings = ctx.obj['settings']
    
    try:
        async with SyncEngine(settings) as sync_engine:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Discovering calendars...", total=None)
                
                google_calendars, icloud_calendars = await sync_engine.calendar_manager.discover_calendars()
        
        # Display Google calendars
        console.print("\n[bold]Google Calendars[/bold]")
        google_table = Table(show_header=True, header_style="bold blue")
        google_table.add_column("Name", style="cyan")
        google_table.add_column("ID", style="dim")
        google_table.add_column("Primary", justify="center")
        google_table.add_column("Access", justify="center")
        
        for cal in google_calendars:
            google_table.add_row(
                cal.name,
                cal.id,
                "✓" if cal.is_primary else "",
                cal.access_role or ""
            )
        console.print(google_table)
        
        # Display iCloud calendars  
        console.print("\n[bold]iCloud Calendars[/bold]")
        icloud_table = Table(show_header=True, header_style="bold green")
        icloud_table.add_column("Name", style="cyan")
        icloud_table.add_column("ID", style="dim")
        icloud_table.add_column("Primary", justify="center")
        
        for cal in icloud_calendars:
            icloud_table.add_row(
                cal.name,
                cal.id[:50] + "..." if len(cal.id) > 50 else cal.id,
                "✓" if cal.is_primary else ""
            )
        console.print(icloud_table)
        
        console.print(f"\nTotal: [blue]{len(google_calendars)} Google[/blue], [green]{len(icloud_calendars)} iCloud[/green] calendars")
        
    except Exception as e:
        console.print(f"[red]Failed to list calendars: {e}[/red]")


@cli.group()
def google():
    """Google-specific utilities."""
    pass


@google.command('watch')
@click.option('--calendar', '-c', required=True, help='Google calendar ID (or name from calendars list)')
@click.option('--address', '-a', required=True, help='Public HTTPS webhook URL for push notifications')
@click.option('--token', '-t', required=True, help='Shared secret to validate webhook (X-Goog-Channel-Token)')
@async_command
async def google_watch(ctx, calendar, address, token):
    """Register a Google push notification channel for a calendar."""
    settings = ctx.obj['settings']
    try:
        async with SyncEngine(settings) as engine:
            # Resolve calendar ID if a friendly name was provided
            cal_id = calendar
            calendars = await engine.google_service.get_calendars()
            for cal in calendars:
                if cal.id == calendar or (cal.name and cal.name.lower() == calendar.lower()):
                    cal_id = cal.id
                    break
            # Channel
            channel_id = str(uuid4())
            body = {
                'id': channel_id,
                'type': 'web_hook',
                'address': address,
                'token': token,
            }
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: engine.google_service.service.events().watch(calendarId=cal_id, body=body).execute()
            )
            # Persist minimal info
            out = {
                'calendarId': cal_id,
                'channelId': result.get('id', channel_id),
                'resourceId': result.get('resourceId'),
                'expiration': result.get('expiration'),
                'address': address,
            }
            path = Path(settings.data_dir) / 'google_channels.json'
            existing = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except Exception:
                    existing = []
            existing = [e for e in existing if e.get('calendarId') != cal_id]
            existing.append(out)
            path.write_text(json.dumps(existing, indent=2))
            console.print(f"[green]✓ Watch registered[/green] → {path}")
            console.print(json.dumps(out, indent=2))
    except Exception as e:
        console.print(f"[red]Failed to register watch: {e}[/red]")
        if settings.debug:
            console.print_exception()
        sys.exit(1)


@google.command('renew')
@click.option('--renew-before-mins', default=1440, type=int, help='Renew channels expiring within this many minutes')
@click.option('--force', is_flag=True, help='Force renew all stored channels regardless of expiration')
@click.option('--token', '-t', help='Shared secret to validate webhook (defaults to GOOGLE_CHANNEL_TOKEN env)')
@async_command
async def google_renew(ctx, renew_before_mins, force, token):
    """Renew existing Google push channels stored in /data/google_channels.json."""
    from datetime import datetime, timezone, timedelta
    settings = ctx.obj['settings']
    path = Path(settings.data_dir) / 'google_channels.json'
    if not path.exists():
        console.print("[yellow]No stored channels found (google_channels.json)\nRun 'google watch' first.[/yellow]")
        return
    try:
        items = json.loads(path.read_text()) or []
    except Exception as e:
        console.print(f"[red]Failed to read channels file: {e}[/red]")
        return

    exp_threshold = datetime.now(timezone.utc) + timedelta(minutes=renew_before_mins)
    updated = []
    secret = token or os.getenv('GOOGLE_CHANNEL_TOKEN')
    if not secret:
        console.print("[yellow]No token provided; set GOOGLE_CHANNEL_TOKEN or pass --token[/yellow]")

    async with SyncEngine(settings) as engine:
        for ch in items:
            cal_id = ch.get('calendarId')
            address = ch.get('address')
            channel_id = ch.get('channelId') or ch.get('id')
            resource_id = ch.get('resourceId')
            expiration = ch.get('expiration')

            exp_dt = None
            if isinstance(expiration, str) and expiration.isdigit():
                try:
                    exp_dt = datetime.fromtimestamp(int(expiration)/1000, tz=timezone.utc)
                except Exception:
                    exp_dt = None
            elif isinstance(expiration, str):
                try:
                    exp_dt = datetime.fromisoformat(expiration)
                except Exception:
                    exp_dt = None

            needs_renew = force or (not exp_dt) or (exp_dt <= exp_threshold)
            if not needs_renew:
                updated.append(ch)
                continue

            # Stop old channel if we have both ids
            if channel_id and resource_id:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: engine.google_service.service.channels().stop(
                            body={'id': channel_id, 'resourceId': resource_id}
                        ).execute()
                    )
                    console.print(f"[green]✓ Stopped channel[/green] {channel_id} for {cal_id}")
                except Exception as e:
                    console.print(f"[yellow]Could not stop channel {channel_id}: {e}[/yellow]")

            # Create new watch
            try:
                new_channel_id = str(uuid4())
                body = {'id': new_channel_id, 'type': 'web_hook', 'address': address}
                if secret:
                    body['token'] = secret
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: engine.google_service.service.events().watch(calendarId=cal_id, body=body).execute()
                )
                ch_new = {
                    'calendarId': cal_id,
                    'channelId': result.get('id', new_channel_id),
                    'resourceId': result.get('resourceId'),
                    'expiration': result.get('expiration'),
                    'address': address,
                }
                updated.append(ch_new)
                console.print(f"[green]✓ Renewed watch[/green] for {cal_id}")
            except Exception as e:
                console.print(f"[red]Failed to renew watch for {cal_id}: {e}[/red]")
                updated.append(ch)  # keep old entry

    try:
        path.write_text(json.dumps(updated, indent=2))
        console.print(f"[green]✓ Updated[/green] {path}")
    except Exception as e:
        console.print(f"[red]Failed to update channels file: {e}[/red]")


@google.command('unwatch')
@click.option('--calendar', '-c', help='Google calendar ID or name to unwatch; if omitted, unwatch all')
@async_command
async def google_unwatch(ctx, calendar):
    """Stop existing channels (optionally filtered by calendar) and remove from storage."""
    settings = ctx.obj['settings']
    path = Path(settings.data_dir) / 'google_channels.json'
    if not path.exists():
        console.print("[yellow]No stored channels found (google_channels.json)[/yellow]")
        return
    try:
        items = json.loads(path.read_text()) or []
    except Exception as e:
        console.print(f"[red]Failed to read channels file: {e}[/red]")
        return

    async with SyncEngine(settings) as engine:
        remaining = []
        for ch in items:
            cal_id = ch.get('calendarId')
            if calendar and (cal_id != calendar):
                # Allow name matching
                match = False
                try:
                    cals = await engine.google_service.get_calendars()
                    for cal in cals:
                        if cal.id == cal_id and cal.name and cal.name.lower() == calendar.lower():
                            match = True
                            break
                except Exception:
                    match = False
                if not match:
                    remaining.append(ch)
                    continue

            channel_id = ch.get('channelId') or ch.get('id')
            resource_id = ch.get('resourceId')
            if channel_id and resource_id:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: engine.google_service.service.channels().stop(
                            body={'id': channel_id, 'resourceId': resource_id}
                        ).execute()
                    )
                    console.print(f"[green]✓ Unwatched[/green] {cal_id}")
                except Exception as e:
                    console.print(f"[yellow]Failed to unwatch {cal_id}: {e}[/yellow]")
            # Do not keep this entry

    try:
        if calendar:
            # Write only remaining if filtered
            path.write_text(json.dumps(remaining, indent=2))
        else:
            # Remove file when all unwatched
            path.unlink(missing_ok=True)
        console.print("[green]✓ Channels storage updated[/green]")
    except Exception as e:
        console.print(f"[yellow]Warning: could not update channels storage: {e}[/yellow]")


@cli.group()
def ids():
    """Global ID management commands."""
    pass


@ids.command('backfill')
@async_command
async def ids_backfill(ctx):
    """Backfill CalSync IDs and mappings by running a full sync once."""
    settings = ctx.obj['settings']
    try:
        async with SyncEngine(settings) as engine:
            await engine.sync_calendars(dry_run=False)
        console.print("[green]✓ Backfill complete[/green]")
    except Exception as e:
        console.print(f"[red]Backfill failed: {e}[/red]")
        if settings.debug:
            console.print_exception()
        sys.exit(1)
        sys.exit(1)


@calendars.command('mappings')
@async_command
async def show_mappings(ctx):
    """Show current calendar mappings."""
    settings = ctx.obj['settings']
    
    try:
        async with SyncEngine(settings) as sync_engine:
            mappings = await sync_engine.calendar_manager.get_all_mappings()
        
        if not mappings:
            console.print("[yellow]No calendar mappings configured[/yellow]")
            console.print("Use [bold]calsync-claude calendars create-mapping[/bold] to create mappings")
            return
        
        # Display mappings table
        table = Table(show_header=True, header_style="bold magenta", title="Calendar Mappings")
        table.add_column("ID", style="dim")
        table.add_column("Google Calendar", style="blue")
        table.add_column("iCloud Calendar", style="green")
        table.add_column("Direction", justify="center")
        table.add_column("Enabled", justify="center")
        table.add_column("Created")
        
        for mapping in mappings:
            direction = "↔️" if mapping.bidirectional else ("→" if mapping.sync_direction == "google_to_icloud" else "←")
            enabled = "✅" if mapping.enabled else "❌"
            
            table.add_row(
                str(mapping.id)[:8],
                mapping.google_calendar_name or mapping.google_calendar_id,
                mapping.icloud_calendar_name or mapping.icloud_calendar_id,
                direction,
                enabled,
                mapping.created_at.strftime("%Y-%m-%d")
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Failed to show mappings: {e}[/red]")
        sys.exit(1)


@calendars.command('create-mapping')
@click.option('--google', '-g', required=True, help='Google calendar ID or name')
@click.option('--icloud', '-i', required=True, help='iCloud calendar name')
@click.option('--bidirectional/--unidirectional', default=True, help='Sync direction')
@click.option('--direction', type=click.Choice(['google_to_icloud', 'icloud_to_google']), 
              help='Sync direction for unidirectional sync')
@click.pass_context
async def create_mapping(ctx, google, icloud, bidirectional, direction):
    """Create a new calendar mapping."""
    settings = ctx.obj['settings']
    
    if not bidirectional and not direction:
        console.print("[red]Must specify --direction for unidirectional sync[/red]")
        sys.exit(1)
    
    try:
        async with SyncEngine(settings) as sync_engine:
            # Discover calendars
            google_calendars, icloud_calendars = await sync_engine.calendar_manager.discover_calendars()
            
            # Find matching calendars
            google_cal = sync_engine.calendar_manager._find_google_calendar(google_calendars, google)
            icloud_cal = sync_engine.calendar_manager._find_icloud_calendar(icloud_calendars, icloud)
            
            if not google_cal:
                console.print(f"[red]Google calendar '{google}' not found[/red]")
                sys.exit(1)
            
            if not icloud_cal:
                console.print(f"[red]iCloud calendar '{icloud}' not found[/red]")
                sys.exit(1)
            
            # Create mapping
            mapping = await sync_engine.calendar_manager.create_calendar_mappings(
                [(google_cal, icloud_cal)],
                bidirectional=bidirectional
            )
            
            if mapping:
                mapping = mapping[0]
                if not bidirectional and direction:
                    await sync_engine.calendar_manager.update_mapping(
                        str(mapping.id), sync_direction=direction
                    )
                
                console.print(f"[green]✓ Created mapping:[/green] {google_cal.name} ↔️ {icloud_cal.name}")
            else:
                console.print("[yellow]Mapping already exists[/yellow]")
        
    except Exception as e:
        console.print(f"[red]Failed to create mapping: {e}[/red]")
        sys.exit(1)


@calendars.command('delete-mapping')
@click.argument('mapping_id')
@click.confirmation_option(prompt='Are you sure you want to delete this mapping?')
@click.pass_context
async def delete_mapping(ctx, mapping_id):
    """Delete a calendar mapping."""
    settings = ctx.obj['settings']
    
    try:
        async with SyncEngine(settings) as sync_engine:
            success = await sync_engine.calendar_manager.delete_mapping(mapping_id)
            
            if success:
                console.print(f"[green]✓ Deleted mapping {mapping_id}[/green]")
            else:
                console.print(f"[red]Mapping {mapping_id} not found[/red]")
                sys.exit(1)
        
    except Exception as e:
        console.print(f"[red]Failed to delete mapping: {e}[/red]")
        sys.exit(1)


@calendars.command('auto-map')
@click.option('--dry-run', '-n', is_flag=True, help='Show what would be mapped without creating')
@async_command
async def auto_map_calendars(ctx, dry_run):
    """Automatically create calendar mappings based on name matching."""
    settings = ctx.obj['settings']
    
    try:
        async with SyncEngine(settings) as sync_engine:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Discovering calendars...", total=None)
                
                google_calendars, icloud_calendars = await sync_engine.calendar_manager.discover_calendars()
                
                progress.update(task, description="Matching calendars...")
                
                match_result = await sync_engine.calendar_manager.auto_match_calendars(
                    google_calendars, icloud_calendars
                )
        
        if match_result.matched_pairs:
            console.print(f"\n[green]Found {len(match_result.matched_pairs)} calendar matches:[/green]")
            
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Google Calendar", style="blue")
            table.add_column("iCloud Calendar", style="green")
            table.add_column("Match Type")
            
            for google_cal, icloud_cal in match_result.matched_pairs:
                # Determine match type
                if google_cal.name.lower() == icloud_cal.name.lower():
                    match_type = "Exact name match"
                elif google_cal.is_primary and icloud_cal.is_primary:
                    match_type = "Primary calendars"
                else:
                    match_type = "Similarity match"
                
                table.add_row(google_cal.name, icloud_cal.name, match_type)
            
            console.print(table)
            
            if not dry_run:
                if Confirm.ask("\nCreate these mappings?"):
                    mappings = await sync_engine.calendar_manager.create_calendar_mappings(
                        match_result.matched_pairs
                    )
                    console.print(f"[green]✓ Created {len(mappings)} calendar mappings[/green]")
                else:
                    console.print("[yellow]Cancelled mapping creation[/yellow]")
        else:
            console.print("[yellow]No calendar matches found[/yellow]")
        
        # Show unmatched calendars
        if match_result.unmatched_google:
            console.print(f"\n[yellow]Unmatched Google calendars ({len(match_result.unmatched_google)}):[/yellow]")
            for cal in match_result.unmatched_google:
                console.print(f"  • {cal.name}")
        
        if match_result.unmatched_icloud:
            console.print(f"\n[yellow]Unmatched iCloud calendars ({len(match_result.unmatched_icloud)}):[/yellow]")
            for cal in match_result.unmatched_icloud:
                console.print(f"  • {cal.name}")
        
    except Exception as e:
        console.print(f"[red]Failed to auto-map calendars: {e}[/red]")
        sys.exit(1)


@cli.command()
@async_command
async def conflicts(ctx):
    """Show and resolve conflicts."""
    settings = ctx.obj['settings']
    
    try:
        db_manager = DatabaseManager(settings)
        
        with db_manager.get_session() as session:
            conflicts = db_manager.get_unresolved_conflicts(session)
        
        if not conflicts:
            console.print("[green]No unresolved conflicts found[/green]")
            return
        
        console.print(f"[yellow]Found {len(conflicts)} unresolved conflicts:[/yellow]")
        
        # Display conflicts in a table
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("ID", style="dim")
        table.add_column("Type")
        table.add_column("Google Event")
        table.add_column("iCloud Event")
        table.add_column("Created")
        
        for conflict in conflicts:
            table.add_row(
                str(conflict.id)[:8],
                conflict.conflict_type,
                conflict.google_event_id or "N/A",
                conflict.icloud_event_id or "N/A",
                conflict.created_at.strftime("%Y-%m-%d %H:%M")
            )
        
        console.print(table)
        
        # Show conflict summary with resolution guidance
        console.print(f"\n[yellow]⚠️  {len(conflicts)} conflicts detected[/yellow]")
        console.print("[dim]Conflicts will be automatically resolved using the configured strategy during sync.[/dim]")
        console.print("[dim]Check sync logs for resolution details.[/dim]")
        
    except Exception as e:
        console.print(f"[red]Failed to get conflicts: {e}[/red]")
        sys.exit(1)


def _display_sync_results(sync_report, compact=False):
    """Display sync results."""
    if compact:
        # Compact display for daemon mode
        total_ops = sync_report.total_operations
        success_rate = sync_report.success_rate * 100
        
        console.print(f"[green]✓ {total_ops} operations, {success_rate:.1f}% success[/green]")
        return
    
    # Full display
    table = Table(show_header=True, header_style="bold magenta", title="Sync Results")
    table.add_column("Direction", style="cyan")
    table.add_column("Created", justify="center")
    table.add_column("Updated", justify="center")
    table.add_column("Deleted", justify="center")
    table.add_column("Skipped", justify="center", style="dim")
    
    table.add_row(
        "Google → iCloud",
        str(sync_report.google_to_icloud_created),
        str(sync_report.google_to_icloud_updated),
        str(sync_report.google_to_icloud_deleted),
        str(sync_report.google_to_icloud_skipped)
    )
    
    table.add_row(
        "iCloud → Google",
        str(sync_report.icloud_to_google_created),
        str(sync_report.icloud_to_google_updated),
        str(sync_report.icloud_to_google_deleted),
        str(sync_report.icloud_to_google_skipped)
    )
    
    console.print(table)
    
    # Summary
    duration = None
    if sync_report.completed_at:
        # Ensure both timestamps are timezone-aware for subtraction
        import pytz
        completed = sync_report.completed_at
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=pytz.UTC)
        
        started = sync_report.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=pytz.UTC)
        
        duration = completed - started
    if duration:
        console.print(f"[dim]Completed in {duration.total_seconds():.1f} seconds[/dim]")


def _display_conflicts(conflicts):
    """Display conflicts."""
    console.print(Panel(
        f"[yellow]Found {len(conflicts)} conflicts requiring attention[/yellow]\n" +
        "Use [bold]calsync-claude conflicts[/bold] to review and resolve them.",
        title="Conflicts Detected",
        border_style="yellow"
    ))


def _display_connection_status(connection_results):
    """Display connection status."""
    table = Table(show_header=True, header_style="bold magenta", title="Connection Status")
    table.add_column("Service", style="cyan")
    table.add_column("Status")
    table.add_column("Calendars", justify="center")
    table.add_column("Sample Events", justify="center")
    
    for service_name, result in connection_results.items():
        if result['success']:
            status = "[green]✓ Connected[/green]"
            calendars = str(result.get('calendar_count', 0))
            events = str(result.get('sample_events', 0))
        else:
            status = f"[red]✗ {result.get('error_type', 'Error')}[/red]"
            calendars = "N/A"
            events = "N/A"
        
        table.add_row(service_name.title(), status, calendars, events)
    
    console.print(table)


def _display_sync_status(sync_status):
    """Display sync status."""
    console.print(f"\n[bold]Sync Statistics[/bold]")
    console.print(f"Total event mappings: {sync_status['total_event_mappings']}")
    console.print(f"Unresolved conflicts: {sync_status['unresolved_conflicts']}")
    
    if sync_status['recent_sessions']:
        console.print(f"\n[bold]Recent Sync Sessions[/bold]")
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Started", style="dim")
        table.add_column("Status")
        table.add_column("Operations", justify="center")
        table.add_column("Dry Run", justify="center")
        
        for session in sync_status['recent_sessions']:
            started = datetime.fromisoformat(session['started_at']).strftime("%m-%d %H:%M")
            status = session['status']
            
            total_ops = sum(
                session['operations']['google_to_icloud'].values()
            ) + sum(
                session['operations']['icloud_to_google'].values()
            )
            
            dry_run = "Yes" if session['dry_run'] else "No"
            
            status_color = {
                'completed': 'green',
                'failed': 'red',
                'running': 'yellow'
            }.get(status, 'white')
            
            table.add_row(
                started,
                f"[{status_color}]{status}[/{status_color}]",
                str(total_ops),
                dry_run
            )
        
        console.print(table)


def _display_test_results(connection_results, google_calendars, icloud_calendars):
    """Display detailed test results."""
    # Connection status
    _display_connection_status(connection_results)
    
    # Calendar details
    console.print(f"\n[bold]Google Calendars[/bold]")
    if google_calendars:
        google_tree = Tree("📅 Google Calendars")
        for cal in google_calendars:
            node = google_tree.add(f"{cal.name} ({'Primary' if cal.is_primary else 'Secondary'})")
            node.add(f"ID: {cal.id}")
            if cal.description:
                node.add(f"Description: {cal.description}")
        console.print(google_tree)
    else:
        console.print("[dim]No Google calendars found[/dim]")
    
    console.print(f"\n[bold]iCloud Calendars[/bold]")
    if icloud_calendars:
        icloud_tree = Tree("📅 iCloud Calendars")
        for cal in icloud_calendars:
            node = icloud_tree.add(f"{cal.name} ({'Primary' if cal.is_primary else 'Secondary'})")
            node.add(f"ID: {cal.id[:50]}..." if len(cal.id) > 50 else f"ID: {cal.id}")
        console.print(icloud_tree)
    else:
        console.print("[dim]No iCloud calendars found[/dim]")


@cli.command()
@click.option('--list', 'list_pairs', is_flag=True, help='List current calendar pairs')
@click.option('--validate', is_flag=True, help='Validate calendar pairs configuration')
@click.option('--migrate', is_flag=True, help='Migrate legacy configuration to calendar pairs')
@click.option('--example', is_flag=True, help='Show example calendar pairs configuration')
def pairs(list_pairs, validate, migrate, example):
    """Manage explicit calendar pairs (replaces cross-product sync)."""
    
    if example:
        console.print(Panel(
            generate_pairs_config_example(),
            title="Calendar Pairs Configuration Example",
            border_style="blue"
        ))
        return
    
    try:
        settings = load_settings()
        
        if migrate:
            console.print("[bold]Migrating legacy configuration to calendar pairs...[/bold]\n")
            
            try:
                pairs = migrate_legacy_config_to_pairs(settings)
                
                if not pairs:
                    console.print("[yellow]No legacy configuration found to migrate.[/yellow]")
                    console.print("Consider creating explicit calendar pairs using the --example option.")
                    return
                
                console.print(f"[green]Successfully migrated {len(pairs)} calendar pairs![/green]\n")
                
                # Display migrated pairs
                for i, pair in enumerate(pairs):
                    console.print(f"[bold]Pair {i+1}:[/bold] {pair}")
                    console.print(f"  Google: {pair.google_calendar_id}")
                    console.print(f"  iCloud: {pair.icloud_calendar_id}")
                    console.print(f"  Direction: {'↔ Bidirectional' if pair.bidirectional else f'→ {pair.sync_direction}'}")
                    console.print(f"  Status: {'✅ Enabled' if pair.enabled else '❌ Disabled'}\n")
                
                console.print(Panel(
                    "Add these pairs to your configuration file under [sync_config.calendar_pairs]\n"
                    "and remove any legacy selected_google_calendars/selected_icloud_calendars settings.",
                    title="Migration Instructions",
                    border_style="green"
                ))
                
            except ValueError as e:
                console.print(Panel(
                    str(e),
                    title="Migration Error",
                    border_style="red"
                ))
                return
            
        elif validate:
            if not settings.sync_config.has_explicit_pairs():
                console.print("[yellow]No explicit calendar pairs configured.[/yellow]")
                console.print("Use --migrate to convert legacy configuration or --example for configuration format.")
                return
            
            console.print("[bold]Validating calendar pairs configuration...[/bold]\n")
            
            async def run_validation():
                async with SyncEngine(settings) as sync_engine:
                    errors = sync_engine.calendar_manager.validate_pairs_configuration(
                        settings.sync_config.get_active_pairs()
                    )
                    
                    if errors:
                        console.print("[red]Validation failed with the following errors:[/red]\n")
                        for error in errors:
                            console.print(f"  ❌ {error}")
                        console.print()
                    else:
                        console.print("[green]✅ All calendar pairs are valid![/green]\n")
                    
                    return len(errors) == 0
            
            valid = asyncio.run(run_validation())
            
            if not valid:
                console.print(Panel(
                    "Fix the validation errors above and run --validate again.",
                    title="Validation Failed",
                    border_style="red"
                ))
                return
        
        elif list_pairs:
            if not settings.sync_config.has_explicit_pairs():
                console.print("[yellow]No explicit calendar pairs configured.[/yellow]")
                
                # Check for legacy config
                if (settings.sync_config.selected_google_calendars or 
                    settings.sync_config.selected_icloud_calendars or 
                    settings.sync_config.calendar_mappings):
                    console.print("Legacy configuration detected. Use --migrate to convert to explicit pairs.")
                else:
                    console.print("Use --example to see configuration format.")
                return
            
            pairs = settings.sync_config.get_active_pairs()
            
            console.print(f"[bold]Configured Calendar Pairs ({len(pairs)} pairs)[/bold]\n")
            
            table = Table(show_header=True, header_style="bold magenta", title="Calendar Pairs")
            table.add_column("Name", style="cyan")
            table.add_column("Google Calendar")
            table.add_column("iCloud Calendar")
            table.add_column("Direction", justify="center")
            table.add_column("Status", justify="center")
            
            for pair in pairs:
                name = pair.name or f"Pair {pair.google_calendar_id[:8]}→{pair.icloud_calendar_id[:8]}"
                google_cal = pair.google_calendar_name or pair.google_calendar_id
                icloud_cal = pair.icloud_calendar_name or pair.icloud_calendar_id[:50] + ("..." if len(pair.icloud_calendar_id) > 50 else "")
                
                if pair.bidirectional:
                    direction = "↔"
                elif pair.sync_direction == "google_to_icloud":
                    direction = "→"
                else:
                    direction = "←"
                
                status = "[green]✅ Enabled[/green]" if pair.enabled else "[red]❌ Disabled[/red]"
                
                table.add_row(name, google_cal, icloud_cal, direction, status)
            
            console.print(table)
        
        else:
            # Default: show configuration status
            if settings.sync_config.has_explicit_pairs():
                pairs = settings.sync_config.get_active_pairs()
                console.print(f"[green]✅ Using explicit calendar pairs ({len(pairs)} configured)[/green]")
                console.print("Use --list to view all pairs or --validate to check configuration.")
            else:
                console.print("[yellow]⚠️  No explicit calendar pairs configured[/yellow]")
                if (settings.sync_config.selected_google_calendars or 
                    settings.sync_config.selected_icloud_calendars or 
                    settings.sync_config.calendar_mappings):
                    console.print("[yellow]Legacy configuration detected. Use --migrate to convert.[/yellow]")
                else:
                    console.print("Use --example to see configuration format.")
    
    except Exception as e:
        console.print(Panel(
            f"Error: {e}",
            title="Configuration Error",
            border_style="red"
        ))



def main():
    """Entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()
