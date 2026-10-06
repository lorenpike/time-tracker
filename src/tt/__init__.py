__version__ = "0.2.0"
__author__ = "Noah Everett"

import json
import re
import tomllib
from datetime import datetime, timedelta
from os import environ
from pathlib import Path
from sys import platform
from typing import NamedTuple, Optional, Tuple

import click
import requests

# ANSI escape codes
BLUE = "\033[94m"
DARK_BLUE = "\033[34m"
LIGHT_BLUE = "\033[38;5;68m"
LIGHT_GREEN = "\033[38;5;158m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
GRAY = "\033[90m"
END = "\033[0m"

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MIN_TIME = timedelta(minutes=15)  # minimum time to record


class Entry(NamedTuple):
    start: datetime
    delta: timedelta
    desc: str
    pid: str | None  # project id


def parse_td(td: str) -> timedelta:
    hours, minutes, seconds = map(int, td.split(":"))
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def dump_td(td: timedelta) -> str:
    hours = td.seconds // 60**2
    minutes = (td.seconds % 60**2) // 60
    seconds = td.seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def parse_entry(line: str) -> Entry:
    data = json.loads(line)
    start = datetime.strptime(data["start"], DATE_FORMAT)
    delta = parse_td(data["delta"])
    desc = data["desc"]
    pid = data["pid"]
    return Entry(start, delta, desc, pid)


def dump_entry(entry: Entry) -> str:
    start, delta, desc, pid = entry
    start = start.strftime(DATE_FORMAT)
    delta = dump_td(delta)
    return json.dumps({"start": start, "delta": delta, "desc": desc, "pid": pid})


class Project(NamedTuple):
    id: str
    name: str
    desc: str
    clockify_id: str | None
    github_url: str | None


class Note(NamedTuple):
    start: datetime
    pid: str | None
    status: str | None
    text: str


def user_data_path() -> Path:
    if override := environ.get("TT_DATA_DIR"):
        return Path(override).expanduser()
    elif platform == "win32":
        return Path(environ["LOCALAPPDATA"]) / "tt"
    elif xdg_data_home := environ.get("XDG_DATA_HOME"):
        return Path(xdg_data_home) / "tt"
    else:
        return Path.home() / ".local" / "share" / "tt"


class Record:
    current_stamp = ".alive"
    log = "log.jsonl"

    def __init__(self, root: Path):
        self.root = root

        self.root.mkdir(parents=True, exist_ok=True)
        if not (root / self.log).exists():
            (root / self.log).touch()

    @classmethod
    def cannonical(cls):
        """Resolve the path"""
        return cls(user_data_path())

    def start(self) -> bool:
        if (self.root / self.current_stamp).exists():
            return False

        time = datetime.now()
        with open(self.root / self.current_stamp, "w") as f:
            f.write(time.strftime(DATE_FORMAT))

        return True

    def current(self) -> Tuple[Optional[datetime], timedelta]:
        path = self.root / self.current_stamp
        if not path.exists():
            return None, timedelta()

        with open(path, "r") as f:
            start = datetime.strptime(f.read(), DATE_FORMAT)

        return start, datetime.now() - start

    def write(self, start: datetime, delta: timedelta, desc: str):
        pid = classify(desc, projects)
        text = dump_entry(Entry(start, max(delta, MIN_TIME), desc, pid))
        with open(self.root / self.log, "a") as f:
            f.write(text + "\n")

    def load(self):
        with open(self.root / self.log, "r") as f:
            return [parse_entry(line) for line in f]

    def stop(self):
        path = self.root / self.current_stamp
        if path.exists():
            path.unlink()


def load_projects() -> list[Project]:
    """Load projects from the config file"""
    config_path = user_data_path() / "projects.toml"
    if not config_path.exists():
        return []

    config = tomllib.loads(config_path.read_text())
    return [
        Project(
            id=p["id"],
            name=p["name"],
            desc=p["desc"],
            clockify_id=p.get("clockify_id"),
            github_url=p.get("github_url"),
        )
        for p in config["projects"]
    ]


NOTE_HEADER = re.compile(
    r"^\[(?P<start>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[AP]M), "
    r"(?P<pid>[^,\]]+)(?:, status: (?P<status>done|archive|undone))?\]$"
)
NOTE_DATE_FORMAT = "%Y-%m-%d %I:%M:%S%p"


def notes_path() -> Path:
    return user_data_path() / "notes.md"


def load_notes() -> list[Note]:
    path = notes_path()
    if not path.exists():
        return []

    notes: list[Note] = []
    current: tuple[datetime, str | None, str | None] | None = None
    body: list[str] = []

    def save_current() -> None:
        if current is None:
            return
        start, pid, status = current
        notes.append(Note(start, pid, status, "\n".join(body).strip("\n")))

    for line in path.read_text(encoding="utf-8").splitlines():
        match = NOTE_HEADER.fullmatch(line.strip())
        if match:
            save_current()
            start = datetime.strptime(match.group("start"), NOTE_DATE_FORMAT)
            pid = match.group("pid")
            current = (start, None if pid == "None" else pid, match.group("status"))
            body = []
        elif current is not None:
            body.append(line)
        elif line.strip():
            raise ValueError(f"Unexpected content before first note header in {path}")

    save_current()
    return notes


def append_note(text: str, pid: str | None, status: str | None) -> None:
    path = notes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(NOTE_DATE_FORMAT)
    header = f"[{timestamp}, {pid or 'None'}"
    if status is not None:
        header += f", status: {status}"
    header += "]"

    with path.open("a", encoding="utf-8", newline="\n") as file:
        if path.stat().st_size:
            file.write("\n")
        file.write(f"{header}\n{text.strip()}\n")


def lookup(pid: str) -> Project | None:
    """Lookup a project by ID"""
    for project in projects:
        if project.id == pid:
            return project
    return None


record = Record.cannonical()
projects = load_projects()
off = max((len(project.name) for project in projects), default=0)


def classify(desc: str, projects: list[Project]) -> str | None:
    """Best-effort Jev project classification; return a project ID or None."""
    if not projects:
        return None

    try:
        choices = {
            project.id: f"{project.name}: {project.desc}" for project in projects
        }
        choices["none"] = "No listed project is a good match"
        response = requests.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": f"Bearer {environ['JEV_API_KEY']}"},
            json={
                "model": "jev-latest",
                "state": {"entry_description": desc},
                "questions": {
                    "project": {
                        "type": "choice",
                        "instructions": (
                            "Choose the project most related to this work description. "
                            "Choose 'none' if no project is a clear fit."
                        ),
                        "criteria": choices,
                    }
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        project_id = response.json()["answers"]["project"]["choice"]
        return project_id if project_id in choices and project_id != "none" else None
    except Exception:  # Best effort
        return None


def classify_note(text: str) -> tuple[str | None, str]:
    """Best-effort project and note/todo classification."""
    try:
        project_choices = {
            project.id: f"{project.name}: {project.desc}" for project in projects
        }
        project_choices["none"] = "No listed project is a good match"
        response = requests.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": f"Bearer {environ['JEV_API_KEY']}"},
            json={
                "model": "jev-latest",
                "state": {"text": text},
                "questions": {
                    "project": {
                        "type": "choice",
                        "instructions": "Choose the project this note or todo relates to, or none.",
                        "criteria": project_choices,
                    },
                    "kind": {
                        "type": "choice",
                        "instructions": "Classify this as a note or an actionable todo.",
                        "criteria": {
                            "note": "A thought, observation, or information to remember",
                            "todo": "An action that needs to be completed",
                        },
                    },
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        answers = response.json()["answers"]
        pid = answers["project"]["choice"]
        kind = answers["kind"]["choice"]
        if pid not in project_choices:
            pid = "none"
        if kind not in ("note", "todo"):
            kind = "note"
        return (None if pid == "none" else pid), kind
    except Exception:  # Best effort
        return None, "note"


@click.group()
@click.version_option(version=__version__)
def cli():
    """Timer tool for Noah"""


@cli.command()
def start():
    """Starts the timer"""
    success = record.start()
    if not success:
        click.echo(f"{RED}Timer already started.{END}")
        return
    click.echo(f"{GRAY}Timer started!{END}")


@cli.command()
def status():
    """Checks the timer status"""
    start, delta = record.current()
    if start is None:
        click.echo(f"{RED}Timer is not currently tracking.{END}")
        return
    time = datetime(year=1, month=1, day=1) + delta
    click.echo(f"{BLUE}Time elapsed: {time:%H:%M:%S}{END}")


@cli.command()
@click.argument("desc", required=False)
def end(desc: str | None):
    """Ends the timer and record entry"""
    start, delta = record.current()
    if start is None:
        click.echo(f"{RED}Timer is not currently tracking.{END}")
        return

    desc = desc or click.edit()
    if desc is None:
        click.echo(f"{RED}No description provided.{END}")
        return
    record.write(start, delta, desc.strip())
    record.stop()
    click.echo(f"{GRAY}Timer stopped!{END}")


@cli.command()
@click.argument("desc", required=False)
def switch(desc: str | None):
    """Switches the timer and record entry"""

    start, delta = record.current()
    if start is None:
        click.echo(f"{RED}Timer is not currently tracking.{END}")
        return

    desc = desc or click.edit()
    if desc is None:
        click.echo(f"{RED}No description provided.{END}")
        return

    record.write(start, delta, desc.strip())
    record.stop()

    record.start()
    click.echo("Switching timer...")


@cli.command()
@click.option("--date", "-d", default=None, help="Date to show")
@click.option(
    "--yesterday",
    "-y",
    count=True,
    help="Show entries N days ago (repeat -y; e.g. -yyyy is 4 days ago)",
)
@click.option("--todo", "todo_only", is_flag=True, help="Show active todos")
@click.option(
    "--edit",
    "-e",
    "edit_file",
    type=click.Choice(["projects", "notes", "log"]),
    help="Open a data file in the configured editor.",
)
def show(
    date: str | None, yesterday: int, edit_file: str | None, todo_only: bool
):
    """Show entries and notes, active todos, or edit a data file."""
    if edit_file is not None:
        paths = {
            "projects": user_data_path() / "projects.toml",
            "notes": notes_path(),
            "log": record.root / record.log,
        }
        path = paths[edit_file]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        click.edit(filename=str(path))
        return

    date_time = (
        datetime.now() - timedelta(days=yesterday)
        if yesterday
        else datetime.now() if date is None else datetime.strptime(date, "%Y-%m-%d")
    )
    if todo_only:
        records = []
        notes = [note for note in load_notes() if note.status == "undone"]
    else:
        records = [
            entry for entry in record.load() if entry.start.date() == date_time.date()
        ]
        notes = [note for note in load_notes() if note.start.date() == date_time.date()]
    if not records and not notes:
        message = (
            "No active todos found."
            if todo_only
            else f"No entries or notes found for date: {date_time:%Y-%m-%d}"
        )
        click.echo(f"{RED}{message}{END}")
        return

    events = [(entry.start, "entry", entry) for entry in records]
    events.extend((note.start, "note", note) for note in notes)
    events.sort(key=lambda event: event[0])
    project_width = max(off, len("Unknown"))
    duration_width = max(
        [len(str(entry.delta)) for entry in records] + [len("Note"), len("Todo"), 8]
    )
    time_format = "%Y-%m-%d %I:%M%p" if todo_only else "%I:%M%p"
    time_width = len(datetime.now().strftime(time_format))
    for start, event_type, item in events:
        time_text = start.strftime(time_format)
        project = next((p.name for p in projects if p.id == item.pid), "Unknown")
        if event_type == "entry":
            marker = str(item.delta)
            text_lines = item.desc.splitlines() or [""]
        else:
            marker = "Note" if item.status is None else "Todo"
            marker_color = {
                None: GRAY,
                "undone": GRAY,
                "done": GREEN,
                "archive": DARK_BLUE,
            }.get(item.status, GRAY)
            text_lines = item.text.splitlines() or [""]
            text_color = LIGHT_BLUE if item.status is None else LIGHT_GREEN

        if event_type == "entry":
            prefix = f"{time_text:<{time_width}} {str(item.delta):>{duration_width}} {project:<{project_width}}  "
            click.echo(
                f"{BLUE}{time_text:<{time_width}} {GREEN}{str(item.delta):>{duration_width}}{YELLOW} "
                f"{project:<{project_width}}  {GRAY}{text_lines[0]}{END}"
            )
        else:
            prefix = f"{time_text:<{time_width}} {marker:^{duration_width}} {project:<{project_width}}  "
            click.echo(
                f"{BLUE}{time_text:<{time_width}} {marker_color}{marker:^{duration_width}}{YELLOW} "
                f"{project:<{project_width}}  {text_color}{text_lines[0]}{END}"
            )
        for line in text_lines[1:]:
            click.echo(f"{' ' * len(prefix)}{text_color}{line}{END}")


@cli.command()
@click.argument("text", required=False)
def add(text: str | None):
    """Add a timestamped note or todo."""
    if text is None:
        text = click.edit()
    if text is None or not text.strip():
        click.echo(f"{RED}No note text provided.{END}")
        return

    text = text.strip()
    pid, kind = classify_note(text)
    status = "undone" if kind == "todo" else None
    append_note(text, pid, status)
    project = next((p.name for p in projects if p.id == pid), "Unknown")
    click.echo(f"{GRAY}Added {kind} for {project}.{END}")


@cli.command()
@click.option("--json", "json_", is_flag=True, help="Output JSON")
@click.option("--reverse", is_flag=True, help="Show recent first")
def log(json_: bool, reverse: bool):
    """Show all entries"""

    curr = None

    for entry in (lambda x: reversed(x) if reverse else x)(record.load()):
        date, *_ = entry
        if curr is None:
            curr = date.date()

        if curr != date.date() and not json_:
            curr = date.date()
            click.echo()

        if json_:
            click.echo(f"{dump_entry(entry)}")
        else:
            start, delta, desc, pid = entry
            project = next((p.name for p in projects if p.id == pid), "Unknown")
            click.echo(
                f"{BLUE}{start:%Y-%m-%d %I:%M%p} {GREEN}{delta}{YELLOW} {project:<{off}}\t{GRAY}{desc}{END}"
            )


@cli.command()
def check():
    """Validate config"""

    click.echo(f"Data dir: {BLUE}{record.root}{END}")
    click.echo(f"Log file: {BLUE}{record.root / record.log}{END}")
    assert len(set(p.id for p in projects)) == len(projects), (
        "Duplicate project IDs found"
    )
    assert all(p.id for p in projects), "Project IDs cannot be empty"
    click.echo(f"Number of projects: {BLUE}{len(projects)}{END}")


if __name__ == "__main__":
    cli()
