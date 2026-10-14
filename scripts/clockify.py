"""
Usage:
    export CLOCKIFY_API_KEY="your_api_key"
    export CLOCKIFY_WORKSPACE_ID="your_workspace_id"
    python clockify.py --from 2024-01-01
"""

import json as _json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from http.client import HTTPConnection, HTTPSConnection
from os import environ

import click
from tqdm import tqdm

from tt import projects, record


@dataclass
class Response:
    """HTTP Response"""

    status_code: int
    content: bytes
    headers: dict

    def json(self):
        return _json.loads(self.content)


def request(url, method, json: dict = None, headers: dict = None):
    """Make an HTTP request"""
    if url.startswith("https://"):
        conn = HTTPSConnection(url[8:].split("/")[0])
    elif url.startswith("http://"):
        conn = HTTPConnection(url[7:].split("/")[0])
    else:
        raise ValueError("URL must start with http:// or https://")

    if headers is None:
        headers = {}

    path = "/" + "/".join(url.split("/")[3:])
    body = None
    if json is not None:
        body = _json.dumps(json)
        headers["Content-Type"] = "application/json"

    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    return Response(response.status, response.read(), dict(response.getheaders()))


get = partial(request, method="GET")
post = partial(request, method="POST")
put = partial(request, method="PUT")
delete = partial(request, method="DELETE")
patch = partial(request, method="PATCH")
head = partial(request, method="HEAD")


def sanitise_description(description: str) -> str:
    """Replace characters that Clockify rejects in descriptions."""
    return description.translate(str.maketrans({"<": "[", ">": "]"}))


last_month = datetime.now() - timedelta(days=30)
clockify_project_ids = {p.id: p.clockify_id for p in projects}

api_key = environ["CLOCKIFY_API_KEY"]
workspaceId = environ["CLOCKIFY_WORKSPACE_ID"]


@click.command()
@click.option(
    "--from",
    "-f",
    "from_",
    default=last_month,
    help="From date unitl now",
    type=click.DateTime(formats=["%Y-%m-%d"]),
)
@click.option("--dry-run", is_flag=True, help="Dry run")
def main(from_: datetime, dry_run: bool):
    """Sync log with clockify"""
    print(f"Syncing {record.root}")
    records = [r for r in record.load() if r.start.date() >= from_.date()]
    archived = get(
        f"https://api.clockify.me/api/v1/workspaces/{workspaceId}/projects?archived=true&page-size=500",
        headers={"X-Api-Key": api_key},
    )
    assert archived.status_code == 200, archived.content
    archived_ids = {p["id"] for p in archived.json()}

    unmapped = uploaded = failed = 0
    for r in tqdm(records):
        project_id = clockify_project_ids.get(r.pid)
        if project_id in archived_ids:
            project_id = None  # Clockify rejects entries on archived projects
        if r.pid is not None and project_id is None:
            unmapped += 1

        data = {
            "billable": True,
            "customattributes": [],
            "type": "REGULAR",
            "tagIds": [],
            "description": sanitise_description(r.desc),
            "start": r.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (r.start + r.delta)
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "projectId": project_id,
            "taskId": None,
        }

        url = f"https://api.clockify.me/api/v1/workspaces/{workspaceId}/time-entries"
        if dry_run:
            print(f"DRY RUN: POST {url} with \n{_json.dumps(data, indent=4)}")
            continue

        response = post(url, json=data, headers={"X-Api-Key": api_key})
        if response.status_code != 201:
            failed += 1
            print(f"FAILED ({response.status_code}) {r.start} {r.desc!r}: {response.content.decode(errors='replace')}")
            continue
        uploaded += 1

    if unmapped:
        print(f"{unmapped} entries uploaded without a project: no clockify_id or archived in Clockify")
    print(f"Uploaded {uploaded}, failed {failed}")


if __name__ == "__main__":
    main()
