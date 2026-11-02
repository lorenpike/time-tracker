import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import tt
from tt import clockify


@pytest.fixture
def setup_sync(tmp_path, monkeypatch):
    record = tt.Record(tmp_path / "data")
    monkeypatch.setattr(clockify, "record", record)
    monkeypatch.setattr(clockify, "projects", [])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(clockify, "user_data_path", lambda: tmp_path / "tt")
    monkeypatch.setenv("CLOCKIFY_API_KEY", "test-key")
    monkeypatch.setenv("CLOCKIFY_WORKSPACE_ID", "workspace")
    entries = [
        tt.Entry(
            datetime(2026, 10, 14, 15, 17, i),
            timedelta(minutes=15),
            f"Work <{i}>",
            None,
            i == 0,
        )
        for i in range(3)
    ]
    path = record.root / record.log
    path.write_text("\n".join(tt.dump_entry(entry) for entry in entries) + "\n")
    calls = []

    def request(session, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return Mock(json=lambda: [])
        return Mock(status_code=201)

    monkeypatch.setattr(clockify, "request", request)
    return record, calls


def test_key_resolution_and_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("CLOCKIFY_API_KEY", " env-key ")
    monkeypatch.setattr(clockify, "user_data_path", lambda: tmp_path / "tt")
    path = tmp_path / "tt" / "clockify-api.txt"
    path.parent.mkdir()
    path.write_text("file-key\n")
    assert clockify.resolve_api_key() == "env-key"
    monkeypatch.delenv("CLOCKIFY_API_KEY")
    assert clockify.resolve_api_key() == "file-key"
    path.unlink()
    # Resolve via a command so Click's hidden-input prompt is exercised.
    import click

    @click.command()
    def resolve():
        click.echo(clockify.resolve_api_key())

    result = CliRunner().invoke(resolve, input="prompt-key\n")
    assert result.exit_code == 0, result.output
    assert path.read_text() == "prompt-key\n"
    assert clockify.resolve_api_key() == "prompt-key"


def test_sync_and_repeat(setup_sync):
    record, calls = setup_sync
    runner = CliRunner()
    result = runner.invoke(tt.cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert all(entry.synced for entry in record.load())
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 2
    assert posts[0][2]["json"]["description"] == "Work [1]"
    calls.clear()
    result = runner.invoke(tt.cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert not calls
    assert not (record.root / ".clockify-sync.lock").exists()


def test_dry_run(setup_sync):
    record, calls = setup_sync
    path = record.root / record.log
    before = path.read_bytes()
    result = CliRunner().invoke(tt.cli, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == before
    assert not any(call[0] == "POST" for call in calls)


def test_partial_failure_persists_success_immediately(setup_sync, monkeypatch):
    record, calls = setup_sync
    original = clockify.request
    posts = 0

    def request(session, method, path, **kwargs):
        nonlocal posts
        if method == "POST":
            posts += 1
            if posts == 2:
                assert record.load()[1].synced is True
                return Mock(status_code=400)
        return original(session, method, path, **kwargs)

    monkeypatch.setattr(clockify, "request", request)
    result = CliRunner().invoke(tt.cli, ["sync"])
    assert result.exit_code == 1
    assert [entry.synced for entry in record.load()] == [True, True, False]


def test_workspace_resolution_and_prompt(setup_sync, tmp_path, monkeypatch):
    _, calls = setup_sync
    path = tmp_path / "tt" / "clockify-workspace.txt"
    path.parent.mkdir()
    path.write_text(" file-workspace \n")
    assert clockify.resolve_workspace_id() == "workspace"
    monkeypatch.setenv("CLOCKIFY_WORKSPACE_ID", " ")
    assert clockify.resolve_workspace_id() == "file-workspace"
    path.write_text(" \n")
    result = CliRunner().invoke(tt.cli, ["sync"], input=" prompt-workspace \n")
    assert result.exit_code == 0, result.output
    assert path.read_text() == "prompt-workspace\n"
    assert clockify.resolve_workspace_id() == "prompt-workspace"
    assert any(
        route == "/workspaces/prompt-workspace/time-entries" for _, route, _ in calls
    )
    assert not any(route == "/user" for _, route, _ in calls)


def test_sync_lock(setup_sync):
    record, calls = setup_sync
    lock = record.root / ".clockify-sync.lock"
    lock.touch()
    result = CliRunner().invoke(tt.cli, ["sync"])
    assert result.exit_code == 1
    assert not calls
    assert lock.exists()


def test_save_preserves_extra_fields_and_appends(setup_sync):
    record, _ = setup_sync
    entries = record.load()
    path = record.root / record.log
    lines = path.read_text().splitlines()
    data = json.loads(lines[1])
    data["extra"] = "preserved"
    lines[1] = json.dumps(data)
    lines.append(tt.dump_entry(entries[2]))
    path.write_text("\n".join(lines) + "\n")
    clockify.mark_synced(1, entries[1])
    saved = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(saved) == 4
    assert saved[1]["extra"] == "preserved"
    assert saved[1]["synced"] is True


def test_save_failure_stops_uploads(setup_sync, monkeypatch):
    _, calls = setup_sync

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(clockify, "mark_synced", fail)
    result = CliRunner().invoke(tt.cli, ["sync"])
    assert result.exit_code == 1
    assert "Check Clockify before retrying" in result.output
    assert sum(method == "POST" for method, _, _ in calls) == 1
