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

from time_tracker import record


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

last_month = datetime.now() - timedelta(days=30)

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
    before = lambda entry: entry[0].date() > from_.date()
    records = [entry for entry in record.load() if before(entry)]
    for r in tqdm(records):
        data = {
            "billable": True,
            "customattributes": [],
            "type": "REGULAR",
            "tagIds": [],
            "description": r[2],
            "start": r[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (r[0] + r[1])
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "projectId": None,
            "taskId": None,
        }

        url = f"https://api.clockify.me/api/v1/workspaces/{workspaceId}/time-entries"
        if dry_run:
            print(f"DRY RUN: POST {url} with \n{_json.dumps(data, indent=4)}")
            break

        response = post(url, json=data, headers={"X-Api-Key": api_key})
        assert response.status_code == 201, response.content


if __name__ == "__main__":
    main()
