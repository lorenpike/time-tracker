"""Upload completed, unsynced entries to Clockify."""

import json
import os
import tempfile
from datetime import timezone
from pathlib import Path

import click
import requests

from tt import parse_entry, projects, record, user_data_path

API_URL = "https://api.clockify.me/api/v1"


def resolve_setting(env_name, filename, label, hide_input=False) -> str:
    if value := os.environ.get(env_name, "").strip():
        return value
    path = user_data_path() / filename
    if path.exists() and (value := path.read_text(encoding="utf-8").strip()):
        return value
    while not (value := click.prompt(label, hide_input=hide_input).strip()):
        click.echo(f"{label} cannot be empty.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return value


def resolve_api_key() -> str:
    return resolve_setting(
        "CLOCKIFY_API_KEY", "clockify-api.txt", "Clockify API key", hide_input=True
    )


def resolve_workspace_id() -> str:
    return resolve_setting(
        "CLOCKIFY_WORKSPACE_ID", "clockify-workspace.txt", "Clockify workspace ID"
    )


def request(session, method, path, **kwargs):
    try:
        response = session.request(method, API_URL + path, timeout=30, **kwargs)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        # Do not include request headers or credentials in errors.
        status = exc.response.status_code if exc.response is not None else None
        detail = f"HTTP {status}" if status else "connection error or timeout"
        raise click.ClickException(f"Clockify {method} {path}: {detail}") from exc


def mark_synced(index, expected):
    """Preserve other fields and entries appended since the upload began."""
    path = record.root / record.log
    original = path.read_bytes()
    lines = original.decode("utf-8").splitlines()
    if index >= len(lines) or parse_entry(lines[index]) != expected:
        raise click.ClickException(
            "Log entry changed during sync; cannot save its synced flag."
        )
    data = json.loads(lines[index])
    data["synced"] = True
    lines[index] = json.dumps(data)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".clockify-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            raise click.ClickException(
                "Log changed while saving sync state; please inspect before retrying."
            )
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def payload(entry, project_id):
    return {
        "billable": True,
        "customattributes": [],
        "type": "REGULAR",
        "tagIds": [],
        "description": entry.desc.translate(str.maketrans({"<": "[", ">": "]"})),
        "start": entry.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (entry.start + entry.delta)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "projectId": project_id,
        "taskId": None,
    }


def sync_entries(dry_run):
    entries = record.load()
    pending = [(i, entry) for i, entry in enumerate(entries) if not entry.synced]
    if not pending:
        click.echo("Everything is already synced.")
        return

    with requests.Session() as session:
        session.headers.update({"X-Api-Key": resolve_api_key()})
        workspace = resolve_workspace_id()

        base = f"/workspaces/{workspace}"
        archived_ids = set()
        page = 1
        while True:
            archived = request(
                session,
                "GET",
                base + "/projects",
                params={"archived": "true", "page-size": 500, "page": page},
            ).json()
            archived_ids.update(project["id"] for project in archived)
            if len(archived) < 500:
                break
            page += 1

        project_ids = {project.id: project.clockify_id for project in projects}
        uploaded = failed = unmapped = 0
        for index, entry in pending:
            project_id = project_ids.get(entry.pid)
            if project_id in archived_ids:
                project_id = None
            if entry.pid is not None and project_id is None:
                unmapped += 1
            data = payload(entry, project_id)
            if dry_run:
                click.echo(f"DRY RUN: {json.dumps(data)}")
                continue
            try:
                response = request(session, "POST", base + "/time-entries", json=data)
            except click.ClickException as exc:
                failed += 1
                click.echo(
                    f"FAILED: {entry.start} {entry.desc!r}: {exc.format_message()}",
                    err=True,
                )
                continue
            if response.status_code != 201:
                failed += 1
                click.echo(
                    f"FAILED: expected HTTP 201, got {response.status_code}", err=True
                )
                continue
            try:
                mark_synced(index, entry)
            except (OSError, click.ClickException) as exc:
                raise click.ClickException(
                    f"Uploaded {entry.start} {entry.desc!r}, but could not save its synced flag. "
                    "Check Clockify before retrying to avoid duplicates. " + str(exc)
                ) from exc
            uploaded += 1

        if unmapped:
            click.echo(
                f"{unmapped} entries without a project: missing mapping or archived project."
            )
        if dry_run:
            click.echo(f"Would upload {len(pending)}; no entries changed.")
        else:
            click.echo(
                f"Uploaded {uploaded}, failed {failed}, already synced {len(entries) - len(pending)}."
            )
        if failed:
            raise click.ClickException(
                "Some entries could not be synced; their flags remain false."
            )


def sync(dry_run=False):
    """Upload unsynced entries to Clockify."""
    lock_path = record.root / ".clockify-sync.lock"
    try:
        lock = lock_path.open("x")
    except FileExistsError as exc:
        raise click.ClickException(
            f"Sync lock exists: {lock_path}. If a previous sync crashed, remove it before retrying."
        ) from exc
    try:
        with lock:
            sync_entries(dry_run)
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        lock_path.unlink(missing_ok=True)
