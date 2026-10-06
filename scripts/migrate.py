"""Migrate the legacy data/log.txt format into tt's user data directory.

Run with: python scripts/migrate.py [--source PATH] [--destination PATH]
The legacy data is left untouched. The destination log must be empty or absent.
"""

from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime, timedelta
from pathlib import Path

from tt import DATE_FORMAT, user_data_path


def convert_entry(line: str, line_number: int) -> str:
    try:
        start_text, duration_text, description_text = line.rstrip("\r\n").split(
            "\t", 2
        )
        start = datetime.strptime(start_text, DATE_FORMAT)
        hours, minutes, seconds = map(int, duration_text.split(":"))
        duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        description = ast.literal_eval(description_text)
        if not isinstance(description, str):
            raise ValueError("description is not a string")
    except (ValueError, SyntaxError) as error:
        raise ValueError(f"Invalid legacy entry on line {line_number}: {error}") from error

    total_seconds = int(duration.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return json.dumps(
        {
            "start": start.strftime(DATE_FORMAT),
            "delta": f"{hours:02}:{minutes:02}:{seconds:02}",
            "desc": description,
            "pid": None,
        }
    )


def migrate(source: Path, destination: Path) -> int:
    old_log = source / "log.txt"
    old_active = source / "_current_stamp"
    new_log = destination / "log.jsonl"
    new_active = destination / ".alive"

    if not old_log.is_file():
        raise FileNotFoundError(f"Legacy log not found: {old_log}")
    if new_log.exists() and new_log.stat().st_size:
        raise FileExistsError(f"Destination log is not empty; refusing to merge: {new_log}")
    if old_active.exists() and new_active.exists():
        raise FileExistsError(f"Destination timer already exists: {new_active}")

    converted = [
        convert_entry(line, number)
        for number, line in enumerate(old_log.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip()
    ]

    active_stamp = None
    if old_active.exists():
        active_stamp = old_active.read_text(encoding="utf-8").strip()
        datetime.strptime(active_stamp, DATE_FORMAT)

    destination.mkdir(parents=True, exist_ok=True)
    new_log.write_text("".join(line + "\n" for line in converted), encoding="utf-8")
    if active_stamp is not None:
        new_active.write_text(active_stamp, encoding="utf-8")

    return len(converted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data",
        help="Legacy data directory (default: project data/)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=user_data_path(),
        help="tt data directory (default: platform user data directory)",
    )
    args = parser.parse_args()

    try:
        count = migrate(args.source, args.destination)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print(f"Migrated {count} entries to {args.destination}")
    if (args.source / "_current_stamp").exists():
        print("Migrated active timer state.")
    print("Legacy files were left unchanged.")


if __name__ == "__main__":
    main()
