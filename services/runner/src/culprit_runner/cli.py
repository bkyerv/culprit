"""Authenticated CLI for running a deployed scenario and dumping its trace."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import google.auth
import requests
from google.api_core.exceptions import NotFound
from google.auth.transport.requests import Request
from google.cloud import firestore, storage, tasks_v2
from google.oauth2 import id_token
from google.protobuf import duration_pb2


def _call(method: str, url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    audience = url.split("/runs", 1)[0]
    token = id_token.fetch_id_token(Request(), audience)
    response = requests.request(
        method,
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=890,
    )
    response.raise_for_status()
    return response.json()


def _call_via_tasks(
    *,
    service_url: str,
    scenario_id: str,
    project: str,
    location: str,
    queue: str,
    bucket: str,
    invoker_service_account: str,
) -> dict[str, Any]:
    run_id = f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    credentials, _ = google.auth.default(quota_project_id=project)
    client = tasks_v2.CloudTasksClient(credentials=credentials)
    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{service_url}/runs",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"scenario_id": scenario_id, "run_id": run_id}).encode(),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=invoker_service_account,
                audience=service_url,
            ),
        ),
        dispatch_deadline=duration_pb2.Duration(seconds=900),
    )
    created = client.create_task(
        parent=client.queue_path(project, location, queue),
        task=task,
    )
    print(f"task={created.name}")

    trace_blob = (
        storage.Client(project=project, credentials=credentials)
        .bucket(bucket)
        .blob(f"runs/{run_id}/artifacts/trace.json")
    )
    run_ref = (
        firestore.Client(project=project, credentials=credentials)
        .collection("runs")
        .document(run_id)
    )
    deadline = time.monotonic() + 890
    while time.monotonic() < deadline:
        try:
            return json.loads(trace_blob.download_as_text(retry=None, timeout=10))
        except NotFound:
            snapshot = run_ref.get()
            if snapshot.exists and snapshot.get("status") == "failed":
                raise RuntimeError(f"recording failed: {snapshot.get('error') or {}}")
            time.sleep(5)
    raise TimeoutError(f"timed out waiting for recorded trace: {run_id}")


def _write_trace(trace: dict[str, Any], output: str | None, gcs_output: str | None) -> None:
    rendered = json.dumps(trace, indent=2, sort_keys=True) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        print(f"trace={path}")
    if gcs_output:
        if not gcs_output.startswith("gs://") or "/" not in gcs_output[5:]:
            raise ValueError("--gcs-output must be gs://bucket/object")
        bucket_name, object_name = gcs_output[5:].split("/", 1)
        storage.Client().bucket(bucket_name).blob(object_name).upload_from_string(
            rendered, content_type="application/json"
        )
        print(f"trace={gcs_output}")
    if not output and not gcs_output:
        print(rendered, end="")


def main() -> None:
    parser = argparse.ArgumentParser(prog="culprit-record")
    parser.add_argument(
        "--service-url",
        default=os.environ.get("CULPRIT_RUNNER_URL"),
        help="IAM-protected deployed culprit-runner URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a scenario end-to-end")
    run_parser.add_argument("scenario_id")
    run_parser.add_argument("--output")
    run_parser.add_argument("--gcs-output")
    run_parser.add_argument("--via-cloud-tasks", action="store_true")
    run_parser.add_argument("--project", default="culprit-6f973")
    run_parser.add_argument("--location", default="us-central1")
    run_parser.add_argument("--queue", default="culprit-recordings")
    run_parser.add_argument("--bucket")
    run_parser.add_argument("--invoker-service-account")

    trace_parser = subparsers.add_parser("trace", help="query and dump an existing trace")
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--output")
    trace_parser.add_argument("--gcs-output")

    args = parser.parse_args()
    if not args.service_url:
        parser.error("--service-url or CULPRIT_RUNNER_URL is required")
    service_url = args.service_url.rstrip("/")
    if args.command == "run":
        if args.via_cloud_tasks:
            invoker = args.invoker_service_account or (
                f"culprit-runner@{args.project}.iam.gserviceaccount.com"
            )
            trace = _call_via_tasks(
                service_url=service_url,
                scenario_id=args.scenario_id,
                project=args.project,
                location=args.location,
                queue=args.queue,
                bucket=args.bucket or f"{args.project}-state",
                invoker_service_account=invoker,
            )
        else:
            trace = _call("POST", f"{service_url}/runs", payload={"scenario_id": args.scenario_id})
    else:
        trace = _call("GET", f"{service_url}/runs/{args.run_id}/trace")
    print(f"run_id={trace['run']['run_id']}")
    _write_trace(trace, args.output, args.gcs_output)


if __name__ == "__main__":
    main()
