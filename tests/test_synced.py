import json
from datetime import datetime, timedelta

import tt
from click.testing import CliRunner


def test_new_entry_defaults_to_unsynced():
    entry = tt.Entry(
        datetime(2026, 10, 14, 15, 17, 24), timedelta(minutes=15), "Work", None
    )
    assert entry.synced is False
    assert json.loads(tt.dump_entry(entry))["synced"] is False


def test_legacy_entry_defaults_to_unsynced():
    line = json.dumps(
        {
            "start": "2026-10-14 15:17:24",
            "delta": "00:15:00",
            "desc": "Work",
            "pid": None,
        }
    )
    assert tt.parse_entry(line).synced is False


def test_synced_status_survives_round_trip():
    for synced in (True, False):
        entry = tt.Entry(
            datetime(2026, 10, 14, 15, 17, 24),
            timedelta(minutes=15),
            "Work",
            None,
            synced,
        )
        assert tt.parse_entry(tt.dump_entry(entry)) == entry


def test_record_write_and_display(tmp_path, monkeypatch):
    record = tt.Record(tmp_path)
    monkeypatch.setattr(tt, "record", record)
    monkeypatch.setattr(tt, "classify", lambda desc, projects: None)
    record.write(datetime(2026, 10, 14, 15, 17, 24), timedelta(minutes=15), "Work")
    data = json.loads((tmp_path / record.log).read_text())
    assert data["synced"] is False
    runner = CliRunner()
    for args in (["log"], ["log", "--json"], ["show", "--date", "2026-10-14"]):
        result = runner.invoke(tt.cli, args)
        assert result.exit_code == 0, result.output
        assert "Work" in result.output
