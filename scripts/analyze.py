"""
Clockify Time Analysis Script

Usage:
    export CLOCKIFY_API_KEY="your_api_key"
    export CLOCKIFY_WORKSPACE_ID="your_workspace_id"
    python scripts/analyze.py --project laserpath --from 2024-01-01 --period weekly
    python scripts/analyze.py --list-projects
"""

from __future__ import annotations

from datetime import datetime, timezone
from os import environ
from sys import path

import click
import matplotlib.pyplot as plt
import pandas as pd

path.append("scripts")
from clockify import get

api_key = environ["CLOCKIFY_API_KEY"]
workspace_id = environ["CLOCKIFY_WORKSPACE_ID"]


def get_headers() -> dict:
    """Return headers for Clockify API requests."""
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


def fetch_projects() -> list[dict]:
    """Fetch all projects from the workspace with pagination."""
    base_url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/projects"
    all_projects = []
    page = 1
    page_size = 500  # Max allowed is 5000, using 500 for efficiency

    while True:
        url = f"{base_url}?page={page}&page-size={page_size}"
        response = get(url, headers=get_headers())
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch projects: {response.content}")

        projects = response.json()
        all_projects.extend(projects)

        if len(projects) < page_size:
            break
        page += 1

    return all_projects


def fetch_users() -> list[dict]:
    """Fetch all users in the workspace with pagination."""
    base_url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/users"
    all_users = []
    page = 1
    page_size = 100

    while True:
        url = f"{base_url}?page={page}&page-size={page_size}"
        response = get(url, headers=get_headers())
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch users: {response.content}")

        users = response.json()
        all_users.extend(users)

        if len(users) < page_size:
            break
        page += 1

    return all_users


def parse_iso_duration(duration_str: str) -> float:
    """Parse ISO 8601 duration string (e.g., 'PT1H30M15S') to hours."""
    if not duration_str or not duration_str.startswith("PT"):
        return 0.0

    duration_str = duration_str[2:]  # Remove 'PT' prefix
    hours = 0.0
    minutes = 0.0
    seconds = 0.0

    # Extract hours
    if "H" in duration_str:
        h_idx = duration_str.index("H")
        hours = float(duration_str[:h_idx])
        duration_str = duration_str[h_idx + 1 :]

    # Extract minutes
    if "M" in duration_str:
        m_idx = duration_str.index("M")
        minutes = float(duration_str[:m_idx])
        duration_str = duration_str[m_idx + 1 :]

    # Extract seconds
    if "S" in duration_str:
        s_idx = duration_str.index("S")
        seconds = float(duration_str[:s_idx])

    return hours + minutes / 60 + seconds / 3600


def fetch_user_time_entries(
    user_id: str,
    start_date: datetime,
    end_date: datetime,
    project_id: str,
) -> list[dict]:
    """Fetch time entries for a single user with pagination."""
    base_url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/user/{user_id}/time-entries"
    all_entries = []
    page = 1
    page_size = 100  # Max allowed by free API

    start_str = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    while True:
        url = f"{base_url}?start={start_str}&end={end_str}&project={project_id}&page={page}&page-size={page_size}"
        response = get(url, headers=get_headers())

        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch time entries: {response.content}")

        entries = response.json()
        all_entries.extend(entries)

        if len(entries) < page_size:
            break
        page += 1

    return all_entries


def fetch_all_time_entries(
    start_date: datetime, end_date: datetime, project_id: str
) -> list[dict]:
    """Fetch time entries for all users in the workspace."""
    users = fetch_users()
    click.echo(f"Found {len(users)} workspace members")

    all_entries = []
    for user in users:
        user_id = user.get("id")
        user_name = user.get("name", "Unknown")

        entries = fetch_user_time_entries(user_id, start_date, end_date, project_id)

        # Add user name to each entry for later processing
        for entry in entries:
            entry["_userName"] = user_name

        if entries:
            click.echo(f"  {user_name}: {len(entries)} entries")
            all_entries.extend(entries)

    return all_entries


def process_entries(entries: list[dict], period: str = "weekly") -> pd.DataFrame:
    """Process time entries and group by period and user.

    Args:
        entries: List of time entry dictionaries from Clockify
        period: 'weekly' or 'monthly'

    Returns:
        DataFrame with time periods as index and users as columns, values are hours.
    """
    if not entries:
        return pd.DataFrame()

    records = []
    for entry in entries:
        user_name = entry.get("_userName", "Unknown")
        time_interval = entry.get("timeInterval", {})
        duration_str = time_interval.get("duration", "")
        date_str = time_interval.get("start", "")

        if not date_str:
            continue

        date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        hours = parse_iso_duration(duration_str)

        if period == "weekly":
            year, week, _ = date.isocalendar()
            period_key = f"{year}-W{week:02d}"
        else:  # monthly
            period_key = date.strftime("%Y-%m")

        records.append({"period": period_key, "user": user_name, "hours": hours})

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    pivot = df.pivot_table(
        index="period", columns="user", values="hours", aggfunc="sum", fill_value=0
    )
    pivot = pivot.sort_index()
    return pivot


def plot_stacked_bar(df: pd.DataFrame, title: str, output: str | None = None):
    """Create a stacked bar chart and pie chart from the DataFrame.

    Args:
        df: DataFrame with time periods as index, users as columns
        title: Chart title
        output: Optional file path to save the plot
    """
    if df.empty:
        click.echo("No data to plot.")
        return

    # Create figure with two subplots: bar chart (left) and pie chart (right)
    fig, (ax_bar, ax_pie) = plt.subplots(1, 2, figsize=(16, 6), width_ratios=[2, 1])

    # Get colormap colors for consistency between charts
    cmap = plt.cm.get_cmap("tab10")
    colors = [cmap(i) for i in range(len(df.columns))]

    # Stacked bar chart
    df.plot(kind="bar", stacked=True, ax=ax_bar, color=colors, legend=False)
    ax_bar.set_title(title)
    ax_bar.set_xlabel("Time Period")
    ax_bar.set_ylabel("Hours")
    ax_bar.tick_params(axis="x", rotation=45)
    for label in ax_bar.get_xticklabels():
        label.set_ha("right")

    # Pie chart of total hours by team member
    totals = df.sum().sort_values(ascending=False)
    ax_pie.pie(
        totals,
        labels=[f"{name}\n({hours:.0f}h)" for name, hours in totals.items()],
        colors=[colors[list(df.columns).index(name)] for name in totals.index],
        autopct="%1.1f%%",
        startangle=90,
    )
    ax_pie.set_title("Total Hours by Team Member")

    # Add legend
    fig.legend(
        df.columns,
        title="Team Members",
        loc="center right",
        bbox_to_anchor=(1.0, 0.5),
    )

    plt.tight_layout()
    plt.subplots_adjust(right=0.85)

    if output:
        plt.savefig(output, dpi=150, bbox_inches="tight")
        click.echo(f"Plot saved to {output}")
    else:
        plt.show()


@click.command()
@click.option(
    "--project",
    "-p",
    default=None,
    help="Project name (case-insensitive partial match)",
)
@click.option(
    "--from",
    "-f",
    "from_",
    required=False,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "-t",
    default=None,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date (YYYY-MM-DD), defaults to today",
)
@click.option(
    "--period",
    type=click.Choice(["weekly", "monthly"]),
    default="weekly",
    help="Grouping period: weekly or monthly",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Save plot to file (e.g., output.png)",
)
@click.option(
    "--list-projects",
    is_flag=True,
    help="List available projects and exit",
)
def main(project, from_, to, period, output, list_projects):
    """Analyze Clockify time entries and generate stacked bar charts."""

    if list_projects:
        click.echo("Fetching projects...")
        projects = fetch_projects()
        click.echo(f"\nFound {len(projects)} projects:\n")
        for p in sorted(projects, key=lambda x: x.get("name", "").lower()):
            name = p.get("name", "Unknown")
            pid = p.get("id", "")
            click.echo(f"  {name} ({pid})")
        return

    if not project:
        raise click.UsageError("--project is required unless using --list-projects")

    if not from_:
        raise click.UsageError("--from is required")

    if to is None:
        to = datetime.now()

    start_date = from_.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
    end_date = to.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    click.echo(f"Fetching projects to find '{project}'...")
    projects = fetch_projects()

    matching = [p for p in projects if project.lower() in p.get("name", "").lower()]

    if not matching:
        click.echo(f"No projects found matching '{project}'")
        click.echo("Use --list-projects to see available projects")
        return

    # Show selected projects (combine if multiple match)
    project_names = [p.get("name") for p in matching]
    if len(matching) == 1:
        click.echo(f"Selected project: {project_names[0]}")
    else:
        click.echo(f"Combining {len(matching)} projects:")
        for name in project_names:
            click.echo(f"  - {name}")

    click.echo(f"Date range: {start_date.date()} to {end_date.date()}")
    click.echo(f"Period: {period}")
    click.echo("Fetching time entries...")

    # Fetch entries for all matching projects
    entries = []
    for proj in matching:
        proj_entries = fetch_all_time_entries(start_date, end_date, proj.get("id"))
        entries.extend(proj_entries)
    click.echo(f"Found {len(entries)} time entries")

    if not entries:
        click.echo("No time entries found for the specified criteria.")
        return

    df = process_entries(entries, period)

    if df.empty:
        click.echo("No data to display after processing.")
        return

    total_hours = df.sum().sum()
    click.echo(f"Total hours: {total_hours:.1f}")
    click.echo(f"\nHours by team member:")
    for user in df.columns:
        user_hours = df[user].sum()
        click.echo(f"  {user}: {user_hours:.1f}h")

    display_name = (
        project_names[0]
        if len(project_names) == 1
        else f"{project} ({len(project_names)} projects)"
    )
    title = f"{display_name} - Hours per {period.replace('ly', '')} ({start_date.date()} to {end_date.date()})"
    plot_stacked_bar(df, title, output)


if __name__ == "__main__":
    main()
