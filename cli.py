import click
from pathlib import Path
import shutil

main_cli = click.Group(name="sicily", help="Sicily — State-Locked Autonomous Agent")
SICILY_HOME = Path.home() / ".sicily"

@main_cli.command()
def init():
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
        click.echo("⚠️  ~/.sicily/ does not exist yet. Run `sicily init` first.")
        return

    click.echo(f"📂 Opening {home} ...")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(home)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", str(home)])
    else:
        subprocess.Popen(["xdg-open", str(home)])


@main_cli.command()
def run():
    """Start the Sicily agent."""
    from main import main
    main()


@main_cli.command()
def start():
    """Start a local terminal session with sandboxed file access."""
    import asyncio
    from Cowork.local_session import run_local_session
    asyncio.run(run_local_session())


@main_cli.command()
def help():
    """Show help."""
    click.echo("\nAvailable commands:")
    click.echo("  init - Initialize the project")
    click.echo("  config - Open the config folder")
    click.echo("  run - Run the agent")
    click.echo("  start - Start a local terminal session")
    