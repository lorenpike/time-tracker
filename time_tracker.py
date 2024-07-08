__version__ = "0.1.0"
__author__ = "Noah Everett"

import click
from ast import literal_eval
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

# ANSI escape codes
BLUE = "\033[94m"
GREEN = "\033[92m"
RED = "\033[91m"
GRAY = "\033[90m"
END = "\033[0m"

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_td(td: str) -> timedelta:
    hours, minutes, seconds = map(int, td.split(":"))
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def dump_td(td: timedelta) -> str:
    hours = td.seconds // 60**2
    minutes = (td.seconds % 60**2) // 60
    seconds = td.seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def parse_entry(line: str) -> Tuple[datetime, timedelta, str]:
    start, delta, desc = line.strip().split("\t", 2)
    start = datetime.strptime(start, DATE_FORMAT)
    delta = parse_td(delta)
    desc = literal_eval(desc)
    return start, delta, desc


def dump_entry(entry: Tuple[datetime, timedelta, str]) -> str:
    start, delta, desc = entry
    return f"{start.strftime(DATE_FORMAT)}\t{dump_td(delta)}\t{desc!r}"


class Record:
    current_stamp = "_current_stamp"
    log = "log.txt"

    def __init__(self, root: Path):
        self.root = root

        if not root.exists():
            root.mkdir()

        if not (root / self.log).exists():
            (root / self.log).touch()

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
        text = dump_entry((start, delta, desc))
        with open(self.root / self.log, "a") as f:
            f.write(text + "\n")

    def load(self):
        with open(self.root / self.log, "r") as f:
            return [parse_entry(line) for line in f]

    def stop(self):
        path = self.root / self.current_stamp
        if path.exists():
            path.unlink()


record = Record(Path(__file__).parent / "data")


@click.group()
@click.version_option(version=__version__)
def cli(): ...


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
def end():
    """Ends the timer and record entry"""
    start, delta = record.current()
    if start is None:
        click.echo(f"{RED}Timer is not currently tracking.{END}")
        return

    desc = click.edit().strip()  # type: ignore
    record.write(start, delta, desc)
    record.stop()
    click.echo(f"{GRAY}Timer stopped!{END}")


@cli.command()
def switch():
    """Switches the timer and record entry"""

    start, delta = record.current()
    if start is None:
        click.echo(f"{RED}Timer is not currently tracking.{END}")
        return

    desc = click.edit().strip()  # type: ignore
    record.write(start, delta, desc)
    record.stop()

    record.start()
    click.echo("Switching timer...")


@cli.command()
@click.option("--date", "-d", default=None, help="Date to show")
def show(date: str):
    """Show entries for a given date"""
    date_time = datetime.now() if date is None else datetime.strptime(date, "%Y-%m-%d")
    on_date = lambda entry: entry[0].date() == date_time.date()
    records = [entry for entry in record.load() if on_date(entry)]
    if not records:
        click.echo(f"{RED}No entries found for date: {date}")
    for start, delta, desc in records:
        click.echo(f"{BLUE}{start:%I:%M%p}\t{GREEN}{delta}\t{GRAY}{desc}{END}")


@cli.command()
def log():
    """Show all entries"""

    def reverse_log():
        curr = None
        for entry in reversed(record.load()):
            date, *_ = entry
            if curr is None:
                curr = date.date()

            if curr != date.date():
                curr = date.date()
                yield "\n"

            yield f"{dump_entry(entry)}\n"

    click.echo_via_pager(reverse_log())


if __name__ == "__main__":
    cli()
