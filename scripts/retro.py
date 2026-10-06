"""Retroactively classify unassigned time entries with Jev.

Run with: uv run python scripts/retro.py [--workers 8]
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import click

from tt import classify, load_projects, user_data_path


def backup_log(log_path: Path) -> Path:
    date = datetime.now().strftime("%Y%m%d")
    backup_path = log_path.with_name(f"{date}--logs.jsonl.bck")
    suffix = 2
    while backup_path.exists():
        backup_path = log_path.with_name(f"{date}--logs-{suffix}.jsonl.bck")
        suffix += 1
    shutil.copy2(log_path, backup_path)
    return backup_path


def write_log_atomically(log_path: Path, lines: list[str]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=log_path.parent,
            prefix=f"{log_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.writelines(line + "\n" for line in lines)
        os.replace(temporary_path, log_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@click.command()
@click.option(
    "--workers",
    type=click.IntRange(min=1, max=32),
    default=8,
    show_default=True,
    help="Maximum number of concurrent Jev requests.",
)
def main(workers: int) -> None:
    """Back up the log, then classify entries that have no project ID."""
    log_path = user_data_path() / "log.jsonl"
    if not log_path.is_file():
        raise click.ClickException(f"Log file not found: {log_path}")

    try:
        backup_path = backup_log(log_path)
    except OSError as error:
        raise click.ClickException(f"Could not back up log: {error}") from error
    click.echo(f"Backup created: {backup_path}")

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in lines]
        projects = load_projects()
        pending = [i for i, entry in enumerate(entries) if not entry.get("pid")]
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as error:
        raise click.ClickException(f"Could not read log or projects: {error}") from error

    if not pending:
        click.echo("No entries without a project ID.")
        return
    if not projects:
        click.echo(f"{len(pending)} entries are unassigned; no projects are configured.")
        return

    click.echo(f"Classifying {len(pending)} entries with {workers} workers...")
    descriptions = [entries[index].get("desc", "") for index in pending]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        project_ids = list(executor.map(lambda desc: classify(desc, projects), descriptions))

    assigned = 0
    for index, project_id in zip(pending, project_ids):
        entries[index]["pid"] = project_id
        assigned += project_id is not None

    updated_lines = [json.dumps(entry) for entry in entries]
    try:
        write_log_atomically(log_path, updated_lines)
    except OSError as error:
        raise click.ClickException(
            f"Could not update log; original backup is at {backup_path}: {error}"
        ) from error

    click.echo(f"Assigned project IDs to {assigned} of {len(pending)} entries.")
    click.echo(f"Left {len(pending) - assigned} entries unassigned.")


if __name__ == "__main__":
    main()
