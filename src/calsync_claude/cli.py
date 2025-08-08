"""Enhanced command-line interface with Rich formatting and advanced features."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.tree import Tree
import structlog

from .config import Settings, load_settings, create_example_config
from .sync_engine import SyncEngine
from .models import ConflictResolution
from .database import DatabaseManager

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


class AsyncGroup(click.Group):
    """Click group that supports async commands."""
    
    def invoke(self, ctx):
        """Invoke the command, handling async commands."""
        if asyncio.iscoroutinefunction(ctx.command.callback):
            return asyncio.run(ctx.command.callback(**ctx.params))
        return super().invoke(ctx)


def async_command(*args, **kwargs):
    """Decorator for async click commands."""
    def decorator(f):
        @click.command(*args, **kwargs)
        @click.pass_context
        def wrapper(ctx, *args, **kwargs):
            return asyncio.run(f(ctx, *args, **kwargs))
        return wrapper
    return decorator


@click.group(cls=AsyncGroup)
@click.version_option(version="2.0.0")
@click.option('--config', '-c', type=click.Path(exists=True), 
              help='Path to configuration file')
@click.option('--debug', is_flag=True, help='Enable debug mode')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.pass_context
def cli(ctx, config, debug, verbose):
    """CalSync Claude - Advanced two-way calendar synchronization.
    
    Synchronize events between Google Calendar and iCloud with advanced
    conflict resolution, async operations, and comprehensive monitoring.
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
@click.option('--dry-run', '-n', is_flag=True, 
              help='Show what would be synced without making changes')
@click.option('--conflict-resolution', '-r',
              type=click.Choice(['manual', 'latest_wins', 'google_wins', 'icloud_wins']),
              help='Conflict resolution strategy')
@click.pass_context
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
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            # Initialize sync engine
            task = progress.add_task("Initializing sync engine...", total=None)
            
            async with SyncEngine(settings) as sync_engine:
                progress.update(task, description="Testing connections...")
                
                # Test connections
                connection_results = await sync_engine.test_connections()
                
                if not connection_results['google']['success']:
                    console.print(f"[red]Google Calendar connection failed: {connection_results['google']['error']}[/red]")
                    sys.exit(1)
                
                if not connection_results['icloud']['success']:
                    console.print(f"[red]iCloud Calendar connection failed: {connection_results['icloud']['error']}[/red]")
                    sys.exit(1)
                
                progress.update(task, description="Synchronizing calendars...")
                
                # Perform sync
                sync_report = await sync_engine.sync_calendars(dry_run=dry_run)
                
                progress.update(task, description="Sync completed")
        
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
@click.pass_context
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
@click.pass_context
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
@click.pass_context
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
@click.pass_context
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
        sys.exit(1)


@calendars.command('mappings')
@click.pass_context
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
@click.pass_context
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
@click.pass_context
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
        
        # TODO: Add interactive conflict resolution
        console.print("\n[dim]Interactive conflict resolution coming soon...[/dim]")
        
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
    duration = sync_report.completed_at - sync_report.started_at if sync_report.completed_at else None
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


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()