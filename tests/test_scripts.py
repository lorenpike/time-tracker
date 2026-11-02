import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", ["pie", "analyze"])
def test_report_help_does_not_resolve_credentials(monkeypatch, script):
    pytest.importorskip("matplotlib")
    if script == "analyze":
        pytest.importorskip("pandas")
    pie = load_script(script)
    resolve = Mock(side_effect=AssertionError("Credentials should not be needed for help"))
    monkeypatch.setattr(pie, "resolve_api_key", resolve)
    monkeypatch.setattr(pie, "resolve_workspace_id", resolve)
    result = CliRunner().invoke(pie.main, ["--help"])
    assert result.exit_code == 0, result.output
    resolve.assert_not_called()


@pytest.mark.parametrize("script", ["pie", "analyze"])
def test_report_fetch_uses_shared_credentials(monkeypatch, script):
    pytest.importorskip("matplotlib")
    if script == "analyze":
        pytest.importorskip("pandas")
    pie = load_script(script)
    monkeypatch.setattr(pie, "resolve_api_key", lambda: "saved-key")
    monkeypatch.setattr(pie, "resolve_workspace_id", lambda: "saved-workspace")
    get = Mock(return_value=Mock(status_code=200, json=lambda: []))
    monkeypatch.setattr(pie, "get", get)
    assert pie.fetch_projects() == []
    assert pie.fetch_users() == []
    date = datetime(2026, 10, 14, tzinfo=timezone.utc)
    if script == "pie":
        assert pie.fetch_user_time_entries_all("user", date, date) == []
    else:
        assert pie.fetch_user_time_entries("user", date, date, "project") == []
        assert "project=project" in get.call_args.args[0]
    assert get.call_count == 3
    for call in get.call_args_list:
        assert "/workspaces/saved-workspace/" in call.args[0]
        assert call.kwargs["headers"]["X-Api-Key"] == "saved-key"
        assert call.kwargs["timeout"] == 30


def setup_retro(tmp_path, monkeypatch):
    retro = load_script("retro")
    monkeypatch.setattr(retro, "user_data_path", lambda: tmp_path)
    monkeypatch.setattr(retro, "load_projects", lambda: [object()])
    path = tmp_path / "log.jsonl"
    entries = [
        {"desc": "Uploaded", "pid": None, "synced": True},
        {"desc": "Pending", "pid": None, "synced": False},
        {"desc": "Legacy", "pid": None},
    ]
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")
    return retro, path


def test_retro_preserves_sync_flags_and_backs_up(tmp_path, monkeypatch):
    retro, path = setup_retro(tmp_path, monkeypatch)
    original = path.read_bytes()
    monkeypatch.setattr(retro, "classify", lambda desc, projects: "project")
    result = CliRunner().invoke(retro.main, ["--workers", "1"])
    assert result.exit_code == 0, result.output
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [entry["synced"] for entry in entries] == [True, False, False]
    assert all(entry["pid"] == "project" for entry in entries)
    assert next(tmp_path.glob("*.bck")).read_bytes() == original


def test_retro_refuses_to_overwrite_changed_log(tmp_path, monkeypatch):
    retro, path = setup_retro(tmp_path, monkeypatch)

    def classify(desc, projects):
        if desc == "Uploaded":
            with path.open("a") as stream:
                stream.write('{"desc": "New", "pid": null, "synced": false}\n')
        return "project"

    monkeypatch.setattr(retro, "classify", classify)
    result = CliRunner().invoke(retro.main, ["--workers", "1"])
    assert result.exit_code == 1
    assert "Log changed" in result.output
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(entries) == 4
    assert entries[0]["pid"] is None
