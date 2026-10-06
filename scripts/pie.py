"""
Clockify Double Ring Pie Chart

Shows project time allocation with per-person breakdown.
Inner ring: Total hours per project
Outer ring: Hours per person within each project

Usage:
    export CLOCKIFY_API_KEY="your_api_key"
    export CLOCKIFY_WORKSPACE_ID="your_workspace_id"
    python scripts/pie.py --from 2024-01-01
    python scripts/pie.py --from 2024-01-01 --to 2024-12-31 -o chart.png
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from os import environ
from sys import path

import click
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import MatplotlibDeprecationWarning

# Suppress matplotlib deprecation warnings
warnings.filterwarnings("ignore", category=MatplotlibDeprecationWarning)

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
    page_size = 500

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

    duration_str = duration_str[2:]
    hours = 0.0
    minutes = 0.0
    seconds = 0.0

    if "H" in duration_str:
        h_idx = duration_str.index("H")
        hours = float(duration_str[:h_idx])
        duration_str = duration_str[h_idx + 1 :]

    if "M" in duration_str:
        m_idx = duration_str.index("M")
        minutes = float(duration_str[:m_idx])
        duration_str = duration_str[m_idx + 1 :]

    if "S" in duration_str:
        s_idx = duration_str.index("S")
        seconds = float(duration_str[:s_idx])

    return hours + minutes / 60 + seconds / 3600


def fetch_user_time_entries_all(
    user_id: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict]:
    """Fetch all time entries for a single user (no project filter)."""
    base_url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/user/{user_id}/time-entries"
    all_entries = []
    page = 1
    page_size = 100

    start_str = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    while True:
        url = f"{base_url}?start={start_str}&end={end_str}&page={page}&page-size={page_size}"
        response = get(url, headers=get_headers())

        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch time entries: {response.content}")

        entries = response.json()
        all_entries.extend(entries)

        if len(entries) < page_size:
            break
        page += 1

    return all_entries


def fetch_all_time_entries_by_project(
    start_date: datetime, end_date: datetime
) -> dict[str, dict[str, float]]:
    """Fetch all time entries and group by project and user.

    Returns:
        Dict mapping project names to dicts of {user_name: hours}
    """
    users = fetch_users()
    projects = fetch_projects()

    # Create project ID to name mapping
    project_map = {p["id"]: p["name"] for p in projects if "id" in p and "name" in p}

    click.echo(f"Found {len(users)} users and {len(projects)} projects")

    # Collect all entries: {project_name: {user_name: hours}}
    project_data: dict[str, dict[str, float]] = {}

    for user in users:
        user_id = user.get("id")
        user_name = user.get("name", "Unknown")

        if not user_id:
            continue

        # Fetch entries without project filter to get all projects
        entries = fetch_user_time_entries_all(user_id, start_date, end_date)

        for entry in entries:
            project_id = entry.get("projectId")
            if not project_id or project_id not in project_map:
                continue

            project_name = project_map[project_id]
            time_interval = entry.get("timeInterval", {})
            duration_str = time_interval.get("duration", "")
            hours = parse_iso_duration(duration_str)

            if hours > 0:
                if project_name not in project_data:
                    project_data[project_name] = {}
                if user_name not in project_data[project_name]:
                    project_data[project_name][user_name] = 0.0
                project_data[project_name][user_name] += hours

        # Show progress
        user_total = sum(
            project_data.get(p, {}).get(user_name, 0) for p in project_data
        )
        if user_total > 0:
            click.echo(f"  {user_name}: {user_total:.1f}h")

    return project_data


def plot_double_ring(
    project_data: dict[str, dict[str, float]],
    title: str,
    output: str | None = None,
    invert: bool = False,
):
    """Create a double ring pie chart.

    Args:
        project_data: Dict mapping project names to dicts of {user: hours}
        title: Chart title
        output: Optional file path to save the plot
        invert: If True, inner=team members, outer=projects
    """
    if not project_data:
        click.echo("No data to plot.")
        return

    fig, ax = plt.subplots(figsize=(12, 10))  # type: ignore[misc]

    # Collect all unique users and assign consistent colors
    all_users = set()
    for user_hours in project_data.values():
        all_users.update(user_hours.keys())
    all_users = sorted(all_users)

    user_cmap = plt.cm.get_cmap("Dark2")
    user_colors = {user: user_cmap(i) for i, user in enumerate(all_users)}

    # Project colors (separate colormap)
    proj_cmap = plt.cm.get_cmap("tab20b")
    project_colors = {name: proj_cmap(i) for i, name in enumerate(project_data.keys())}

    # Calculate user totals
    user_totals: dict[str, float] = {user: 0.0 for user in all_users}
    for user_hours in project_data.values():
        for user, hours in user_hours.items():
            user_totals[user] += hours

    # Build user_data: {user: {project: hours}} for inverted view
    user_data: dict[str, dict[str, float]] = {user: {} for user in all_users}
    for project_name, user_hours in project_data.items():
        for user_name, hours in user_hours.items():
            user_data[user_name][project_name] = hours

    # Sort users by total hours
    sorted_users = sorted(user_totals.items(), key=lambda x: -x[1])

    if not invert:
        # Default: inner=projects, outer=users per project
        inner_sizes = []
        inner_colors = []
        inner_labels_data = []  # For hover: (name, hours)

        outer_sizes = []
        outer_colors = []
        outer_labels_data = []  # For hover: (detail_name, parent_name, hours)

        for project_name, user_hours in project_data.items():
            project_total = sum(user_hours.values())
            inner_sizes.append(project_total)
            inner_colors.append(project_colors[project_name])
            inner_labels_data.append((project_name, project_total))

            for user_name, hours in sorted(user_hours.items(), key=lambda x: -x[1]):
                outer_sizes.append(hours)
                outer_colors.append(user_colors[user_name])
                outer_labels_data.append((user_name, project_name, hours))

        legend_patches = [
            plt.Rectangle((0, 0), 1, 1, fc=user_colors[user]) for user, _ in sorted_users
        ]
        legend_labels = [f"{user} ({hours:.0f}h)" for user, hours in sorted_users]
        legend_title = "Team Members"
    else:
        # Inverted: inner=users, outer=projects per user
        inner_sizes = []
        inner_colors = []
        inner_labels_data = []

        outer_sizes = []
        outer_colors = []
        outer_labels_data = []

        for user_name, _ in sorted_users:
            user_total = user_totals[user_name]
            inner_sizes.append(user_total)
            inner_colors.append(user_colors[user_name])
            inner_labels_data.append((user_name, user_total))

            user_projects = user_data[user_name]
            for project_name, hours in sorted(user_projects.items(), key=lambda x: -x[1]):
                outer_sizes.append(hours)
                outer_colors.append(project_colors[project_name])
                outer_labels_data.append((project_name, user_name, hours))

        legend_patches = []
        legend_labels = []
        legend_title = ""

    # Draw outer ring - no labels
    wedges_outer = ax.pie(  # type: ignore[union-attr]
        outer_sizes,
        colors=outer_colors,
        radius=1.0,
        wedgeprops=dict(width=0.3, edgecolor="white", linewidth=0.5),
    )[0]

    # Draw inner ring with percentages
    # Ring goes from 0.35 to 0.7, center at 0.525, so pctdistance = 0.525/0.7 = 0.75
    wedges_inner, _, autotexts = ax.pie(  # type: ignore[union-attr]
        inner_sizes,
        colors=inner_colors,
        radius=0.7,
        autopct=lambda p: f"{p:.0f}%" if p > 3 else "",
        pctdistance=0.75,
        wedgeprops=dict(width=0.35, edgecolor="white", linewidth=1),
    )

    for autotext in autotexts:
        autotext.set_fontsize(8)
        autotext.set_fontweight("bold")

    ax.set_title(title, fontsize=14, fontweight="bold", pad=20)  # type: ignore[union-attr]

    if legend_patches:
        ax.legend(  # type: ignore[union-attr]
            legend_patches,
            legend_labels,
            title=legend_title,
            loc="center left",
            bbox_to_anchor=(1.02, 0.5),
            fontsize=9,
        )

    # Add center text with total hours
    total_hours = sum(inner_sizes)
    ax.text(
        0,
        0,
        f"Total\n{total_hours:.0f}h",
        ha="center",
        va="center",
        fontsize=12,
        fontweight="bold",
    )  # type: ignore[union-attr]

    # Create hover annotation
    annot = ax.annotate(  # type: ignore[union-attr]
        "",
        xy=(0, 0),
        xytext=(20, 20),
        textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="gray", alpha=0.9),
        fontsize=10,
    )
    annot.set_visible(False)

    def on_hover(event):
        if event.inaxes != ax:
            annot.set_visible(False)
            fig.canvas.draw_idle()
            return

        # Check outer ring (users)
        for i, wedge in enumerate(wedges_outer):
            if wedge.contains_point([event.x, event.y]):
                user_name, project_name, hours = outer_labels_data[i]
                annot.set_text(f"{user_name}\n{project_name}\n{hours:.1f}h")
                theta1, theta2 = wedge.theta1, wedge.theta2
                angle = (theta1 + theta2) / 2
                r = 1.0 - 0.15
                x = r * np.cos(np.radians(angle))
                y = r * np.sin(np.radians(angle))
                annot.xy = (x, y)
                annot.set_visible(True)
                fig.canvas.draw_idle()
                return

        # Check inner ring (projects)
        for i, wedge in enumerate(wedges_inner):
            if wedge.contains_point([event.x, event.y]):
                project_name, hours = inner_labels_data[i]
                annot.set_text(f"{project_name}\n{hours:.1f}h")
                theta1, theta2 = wedge.theta1, wedge.theta2
                angle = (theta1 + theta2) / 2
                r = 0.7 - 0.175
                x = r * np.cos(np.radians(angle))
                y = r * np.sin(np.radians(angle))
                annot.xy = (x, y)
                annot.set_visible(True)
                fig.canvas.draw_idle()
                return

        annot.set_visible(False)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("motion_notify_event", on_hover)

    plt.tight_layout()

    if output:
        plt.savefig(output, dpi=150, bbox_inches="tight")
        click.echo(f"Plot saved to {output}")
    else:
        plt.show()


@click.command()
@click.option(
    "--from",
    "-f",
    "from_",
    required=True,
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
    "--output",
    "-o",
    default=None,
    help="Save plot to file (e.g., output.png)",
)
@click.option(
    "--invert",
    "-i",
    is_flag=True,
    help="Invert rings: inner=team members, outer=projects",
)
def main(from_, to, output, invert):
    """Generate a double ring pie chart showing all project and person time allocation."""

    if to is None:
        to = datetime.now()

    start_date = from_.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
    end_date = to.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    click.echo(
        f"Fetching time entries from {start_date.date()} to {end_date.date()}..."
    )
    project_data = fetch_all_time_entries_by_project(start_date, end_date)

    if not project_data:
        click.echo("No data to display.")
        return

    # Sort projects by total hours (descending) and limit to top 20
    sorted_projects = sorted(
        project_data.items(), key=lambda x: sum(x[1].values()), reverse=True
    )
    top_projects = dict(sorted_projects[:20])

    total_hours = sum(sum(hours.values()) for hours in project_data.values())
    top_hours = sum(sum(hours.values()) for hours in top_projects.values())
    click.echo(f"\nTotal: {total_hours:.1f}h across {len(project_data)} projects")
    if len(project_data) > 20:
        click.echo(f"Showing top 20 projects ({top_hours:.1f}h)")

    project_data = top_projects

    title = f"Time Allocation ({start_date.date()} to {end_date.date()})"
    plot_double_ring(project_data, title, output, invert)


if __name__ == "__main__":
    main()
