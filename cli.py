import click
import shutil
import json
import subprocess
from pathlib import Path

from configuration import REQUIRED_KEYS

try:
    from importlib.metadata import version as pkg_version
    __version__ = pkg_version("sicily")
except Exception:
    __version__ = "unknown"

# Folders managed by Sicily (reset and uninstall both use this list)
MANAGED_FOLDERS = ["Souls", "Context", "Recurring_Tasks", "file-index"]

main_cli = click.Group(name="sicily", help="Sicily — State-Locked Autonomous Agent")
SICILY_HOME = Path.home() / ".sicily"
SETTINGS_PATH = SICILY_HOME / "settings.json"


def ensure_initialized(required_keys=None):
    """Validates that init has been run and specified keys are configured."""
    if not SICILY_HOME.exists() or not SETTINGS_PATH.exists():
        click.secho("  Sicily is not initialized yet!", fg="yellow", bold=True)
        click.echo("Please run the initialization command first:")
        click.secho("  sicily init", fg="cyan")
        raise click.Abort()

    # Default to all keys if none specified
    if required_keys is None:
        required_keys = REQUIRED_KEYS

    try:
        with open(SETTINGS_PATH, "r") as f:
            settings = json.load(f)

        missing_or_placeholder = []
        for key in required_keys:
            val = settings.get(key, "")
            if not val or "your_" in val.lower() or "YOUR_" in val:
                missing_or_placeholder.append(key)

        if missing_or_placeholder:
            click.secho("  Configuration incomplete!", fg="yellow", bold=True)
            click.echo(f"The following keys in `{SETTINGS_PATH}` need to be configured:")
            for key in missing_or_placeholder:
                click.secho(f"  - {key}", fg="red")
            click.echo("\nPlease edit your settings file or run:")
            click.secho("  sicily config", fg="cyan")
            raise click.Abort()

    except json.JSONDecodeError:
        click.secho(" Error: `settings.json` is malformed or corrupted.", fg="red", bold=True)
        raise click.Abort()


@main_cli.command()
@click.version_option(version=__version__, prog_name="sicily")
def version():
    """Show the installed Sicily version."""
    pass  # --version flag is handled by @click.version_option


# Attach --version directly to the group so `sicily --version` works too
main_cli = click.Group(
    name="sicily",
    help="Sicily — State-Locked Autonomous Agent",
    params=[
        click.Option(
            ["--version"],
            is_flag=True,
            is_eager=True,
            expose_value=False,
            callback=lambda ctx, param, value: (
                click.echo(f"sicily {__version__}") or ctx.exit()
            ) if value else None,
            help="Show the version and exit.",
        )
    ],
)


@main_cli.command()
def init():
    """Initialize Sicily config, Souls, Context, and task files."""
    package_dir = Path(__file__).resolve().parent
    home = SICILY_HOME
    home.mkdir(exist_ok=True)

    src = package_dir / "settings.example.json"
    dest = home / "settings.json"
    if dest.exists():
        click.echo("~/.sicily/settings.json already exists, skipping.")
    else:
        shutil.copy(src, dest)
        click.echo("Created ~/.sicily/settings.json")

    for folder in ["Souls", "Context"]:
        if (home / folder).exists():
            click.echo(f"~/.sicily/{folder}/ already exists, skipping.")
        else:
            shutil.copytree(package_dir / folder, home / folder)
            click.echo(f"Created ~/.sicily/{folder}/")

    rt_dir = home / "Recurring_Tasks"
    yaml_dest = rt_dir / "recurring_tasks.yaml"
    if yaml_dest.exists():
        click.echo("~/.sicily/Recurring_Tasks/recurring_tasks.yaml already exists, skipping.")
    else:
        rt_dir.mkdir(exist_ok=True)
        shutil.copy(package_dir / "Recurring_Tasks" / "recurring_tasks.yaml", yaml_dest)
        click.echo("Created ~/.sicily/Recurring_Tasks/recurring_tasks.yaml")

    click.echo("\nNext steps:")
    click.echo("  1. Use `sicily config` to open the config folder")
    click.echo("  2. Edit Souls/*.md to define your agent's personality")
    click.echo("  3. Fill settings.json")
    click.echo("  4. sicily run")


@main_cli.command()
def config():
    """Open the ~/.sicily/ config folder in your file manager."""
    import subprocess
    import sys

    home = SICILY_HOME
    if not home.exists():
        click.echo("  ~/.sicily/ does not exist yet. Run `sicily init` first.")
        return

    click.echo(f" Opening {home} ...")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(home)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", str(home)])
    else:
        subprocess.Popen(["xdg-open", str(home)])


@main_cli.command()
def run():
    """Start the Sicily agent."""
    ensure_initialized()
    from main import main
    main()


@main_cli.command()
def start():
    """Start a local terminal session with sandboxed file access."""
    ensure_initialized(required_keys=["OPENAI_API_KEY"])
    import asyncio
    from Cowork.cowork_session import run_local_session
    asyncio.run(run_local_session())


@main_cli.command()
def navigator():
    """Start the Sicily Navigator backend for the browser extension."""
    # Only require the OpenAI API key for this specific tool
    ensure_initialized(required_keys=["OPENAI_API_KEY"])
    
    import uvicorn
    import click
    
    click.secho("\n  Starting Sicily Navigator on http://127.0.0.1:8765", fg="green", bold=True)
    click.echo("  Waiting for extension requests...\n")
    
    # Run uvicorn programmatically (reload=False is safer/cleaner for production)
    uvicorn.run(
        "Navigator.navigator_bridge:app", 
        host="127.0.0.1", 
        port=8765, 
        log_level="info"
    )


@main_cli.command(name="reset")
def reset():
    """Reset all config, personalization, and indexed files back to default."""
    if not SICILY_HOME.exists():
        click.secho("  Nothing to reset — ~/.sicily/ does not exist.", fg="yellow")
        return

    click.secho("\n  This will permanently reset:", fg="yellow", bold=True)
    click.echo("  • settings.json          (wiped and restored from template)")
    click.echo("  • Souls/                 (all personality files reset)")
    click.echo("  • Context/               (all context files reset)")
    click.echo("  • Recurring_Tasks/       (task schedule reset)")
    click.echo("  • file-index/            (ChromaDB, TF-IDF index, registry — deleted)")
    click.echo()

    if not click.confirm("  Are you sure you want to reset everything?", default=False):
        click.echo("Aborted.")
        return

    package_dir = Path(__file__).resolve().parent
    home = SICILY_HOME
    errors = []

    # Reset settings.json
    src = package_dir / "settings.example.json"
    dest = home / "settings.json"
    try:
        if src.exists():
            shutil.copy(src, dest)
            click.echo("  ✓ Reset settings.json")
        else:
            click.secho("  ✗ settings.example.json not found in package — skipping.", fg="yellow")
    except Exception as e:
        errors.append(f"settings.json: {e}")

    # Reset Souls/ and Context/
    for folder in ["Souls", "Context"]:
        target = home / folder
        src_folder = package_dir / folder
        try:
            if target.exists():
                shutil.rmtree(target)
            if src_folder.exists():
                shutil.copytree(src_folder, target)
                click.echo(f"  ✓ Reset {folder}/")
            else:
                click.secho(f"  ✗ {folder}/ not found in package — skipping.", fg="yellow")
        except Exception as e:
            errors.append(f"{folder}/: {e}")

    # Reset Recurring_Tasks/
    rt_dir = home / "Recurring_Tasks"
    yaml_src = package_dir / "Recurring_Tasks" / "recurring_tasks.yaml"
    yaml_dest = rt_dir / "recurring_tasks.yaml"
    try:
        rt_dir.mkdir(exist_ok=True)
        if yaml_src.exists():
            shutil.copy(yaml_src, yaml_dest)
            click.echo("  ✓ Reset Recurring_Tasks/recurring_tasks.yaml")
        else:
            click.secho("  ✗ recurring_tasks.yaml not found in package — skipping.", fg="yellow")
    except Exception as e:
        errors.append(f"Recurring_Tasks/: {e}")

    # Delete file-index/ entirely
    file_index = home / "file-index"
    try:
        if file_index.exists():
            shutil.rmtree(file_index)
            click.echo("  ✓ Deleted file-index/ (ChromaDB, TF-IDF, registry)")
        else:
            click.echo("  ✓ file-index/ did not exist — nothing to remove")
    except Exception as e:
        errors.append(f"file-index/: {e}")

    if errors:
        click.secho("\n  Some items could not be reset:", fg="red", bold=True)
        for err in errors:
            click.secho(f"  ✗ {err}", fg="red")
    else:
        click.secho("\n  Reset complete.", fg="green", bold=True)
        click.echo("Run `sicily init` to re-initialize, then fill in your settings.")


@main_cli.command()
def update():
    """Update Sicily to the latest published version."""
    uv_path = shutil.which("uv")
    if not uv_path:
        click.secho("  uv not found.", fg="red", bold=True)
        click.echo("Sicily is managed via uv. Install it from https://docs.astral.sh/uv/")
        click.secho("  Then run:  uv tool install --reinstall sicily", fg="cyan")
        return

    click.echo("  Checking for updates...")
    result = subprocess.run(
        [uv_path, "tool", "install", "--reinstall", "sicily"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        click.secho("  ✓ Sicily updated successfully.", fg="green")
    else:
        click.secho("  ✗ Update failed. Try running manually:", fg="red")
        click.secho("      uv tool install --reinstall sicily", fg="cyan")
        if result.stderr:
            click.echo(f"  uv error: {result.stderr.strip()}")


@main_cli.command()
def uninstall():
    """Remove all local Sicily files and uninstall the package."""
    click.secho("\n  This will permanently:", fg="yellow", bold=True)
    click.echo("  • Delete ~/.sicily/  (all config, Souls, Context, indexes — everything)")
    click.echo("  • Run:  pip uninstall sicily -y")
    click.echo()

    if not click.confirm("  Are you sure you want to uninstall Sicily?", default=False):
        click.echo("Aborted.")
        return

    # Remove ~/.sicily/
    if SICILY_HOME.exists():
        try:
            shutil.rmtree(SICILY_HOME)
            click.secho("  ✓ Deleted ~/.sicily/", fg="green")
        except Exception as e:
            click.secho(f"  ✗ Could not delete ~/.sicily/: {e}", fg="red")
    else:
        click.echo("  ✓ ~/.sicily/ did not exist — nothing to remove")

    # Uninstall the package — prefer uv (the recommended install method),
    # fall back to pip for anyone who installed via pip directly.
    import subprocess
    import sys

    uv_path = shutil.which("uv")
    if uv_path:
        click.echo("  Uninstalling sicily via uv...")
        result = subprocess.run(
            [uv_path, "tool", "uninstall", "sicily"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.secho("  ✓ sicily uninstalled successfully.", fg="green")
        else:
            click.secho("  ✗ uv tool uninstall failed. Try running manually:", fg="red")
            click.secho("      uv tool uninstall sicily", fg="cyan")
            if result.stderr:
                click.echo(f"  uv error: {result.stderr.strip()}")
    else:
        click.echo("  uv not found — falling back to pip...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "sicily", "-y"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.secho("  ✓ sicily uninstalled successfully.", fg="green")
        else:
            click.secho("  ✗ pip uninstall failed. Try running manually:", fg="red")
            click.secho("      pip uninstall sicily -y", fg="cyan")
            if result.stderr:
                click.echo(f"  pip error: {result.stderr.strip()}")


@main_cli.command(name="usage")
@click.option("--session", is_flag=True, help="Show usage for the last recorded session.")
@click.option("--day", is_flag=True, help="Show usage for the last 24 hours.")
@click.option("--week", is_flag=True, help="Show usage for the last 7 days (default).")
def usage(session, day, week):
    """Show token usage and estimated cost."""
    from usage_tracker import get_usage_report, init_db, cleanup_old_records
    from rich.console import Console
    from rich.table import Table

    init_db()
    cleanup_old_records()

    if session:
        timeframe = "session"
        title_suffix = "Last Session"
    elif day:
        timeframe = "day"
        title_suffix = "Last 24 Hours"
    else:
        timeframe = "week"
        title_suffix = "Last 7 Days"

    report = get_usage_report(timeframe=timeframe)
    console = Console()
    
    if not report:
        console.print(f"\n[yellow]No usage data found for: {title_suffix}[/yellow]\n")
        return

    table = Table(title=f"Sicily Usage Report ({title_suffix})")
    table.add_column("Dimension", style="cyan")
    table.add_column("Model", style="magenta")
    table.add_column("Input Tokens", justify="right", style="green")
    table.add_column("Output Tokens", justify="right", style="green")
    table.add_column("Est. Cost (USD)", justify="right", style="bold yellow")

    total_cost = 0.0
    for row in report:
        table.add_row(
            row["dimension"].capitalize(),
            row["model_name"],
            f"{row['in_tokens']:,}",
            f"{row['out_tokens']:,}",
            f"${row['total_cost']:.5f}"
        )
        total_cost += row['total_cost']

    console.print()
    console.print(table)
    console.print(f"[bold right]Total Estimated Cost: ${total_cost:.5f}[/bold right]\n")


@main_cli.command()
def help():
    """Show help."""
    click.echo("\nAvailable commands:")
    click.echo("  --version     - Show the installed version")
    click.echo("  init          - Initialize the project")
    click.echo("  config        - Open the config folder")
    click.echo("  run           - Run the agent")
    click.echo("  start         - Start a local terminal session")
    click.echo("  navigator     - Start the browser extension backend")
    click.echo("  usage         - Show token usage and estimated cost")
    click.echo("  update        - Update Sicily to the latest version")
    click.echo("  reset         - Reset all config and indexes back to default")
    click.echo("  uninstall     - Remove all local files and uninstall sicily")