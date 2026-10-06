__version__ = "0.2.0"
__author__ = "Noah Everett"

import json
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
def show(date: str | None):
    """Show entries for a given date"""
    date_time = datetime.now() if date is None else datetime.strptime(date, "%Y-%m-%d")
    on_date = lambda entry: entry[0].date() == date_time.date()
    records = [entry for entry in record.load() if on_date(entry)]
    if not records:
        click.echo(f"{RED}No entries found for date: {date}")
    for start, delta, desc, pid in records:
        project = next((p.name for p in projects if p.id == pid), "Unknown")
        click.echo(
            f"{BLUE}{start:%I:%M%p} {GREEN}{delta}{YELLOW} {project:<{off}} {GRAY}{desc}{END}"
        )


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
            click.echo_via_pager
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
