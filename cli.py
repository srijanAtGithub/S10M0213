from click import click
from pathlib import Path
import shutil

main_cli = click.Group(name="s10m0213", help="S10M0213 — State-Locked Autonomous Agent")

@main_cli.command()
def init():
    package_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()

    # settings.example.json
    src = package_dir / "settings.example.json"
    dest = cwd / "settings.example.json"
    if dest.exists():
        click.echo("⚠️  settings.example.json already exists, skipping.")
    else:
        shutil.copy(src, dest)
        click.echo("✅ Created settings.example.json")

    # Souls/
    if (cwd / "Souls").exists():
        click.echo("⚠️  Souls/ already exists, skipping.")
    else:
        shutil.copytree(package_dir / "Souls", cwd / "Souls")
        click.echo("✅ Created Souls/")

    # Context/
    if (cwd / "Context").exists():
        click.echo("⚠️  Context/ already exists, skipping.")
    else:
        shutil.copytree(package_dir / "Context", cwd / "Context")
        click.echo("✅ Created Context/")

    # Recurring_Tasks/recurring_tasks.yaml
    rt_dir = cwd / "Recurring_Tasks"
    yaml_dest = rt_dir / "recurring_tasks.yaml"
    if yaml_dest.exists():
        click.echo("⚠️  Recurring_Tasks/recurring_tasks.yaml already exists, skipping.")
    else:
        rt_dir.mkdir(exist_ok=True)
        shutil.copy(package_dir / "Recurring_Tasks" / "recurring_tasks.yaml", yaml_dest)
        click.echo("✅ Created Recurring_Tasks/recurring_tasks.yaml")

    click.echo("\n📝 Next steps:")
    click.echo("  1. Edit Souls/*.md to define your agent's personality")
    click.echo("  2. Fill in settings.example.json and rename it to settings.json")
    click.echo("  3. s10m0213 run")
