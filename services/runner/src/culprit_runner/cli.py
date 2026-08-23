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

from culprit_runner.investigation import DEFAULT_AUTONOMOUS_INVESTIGATION_SPEND_CAP_USD


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


def _enqueue_http_task(
    *,
    client: tasks_v2.CloudTasksClient,
    service_url: str,
    path: str,
    payload: dict[str, Any],
    project: str,
    location: str,
    queue: str,
    invoker_service_account: str,
) -> str:
    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{service_url}{path}",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode(),
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
    return created.name


def _investigate_via_tasks(
    *,
    service_url: str,
    run_id: str,
    investigation_id: str,
    project: str,
    location: str,
    queue: str,
    invoker_service_account: str,
    branch_spend_cap_usd: float,
    investigation_spend_cap_usd: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    credentials, _ = google.auth.default(quota_project_id=project)
    tasks_client = tasks_v2.CloudTasksClient(credentials=credentials)
    firestore_client = firestore.Client(project=project, credentials=credentials)
    investigation_ref = firestore_client.collection("investigations").document(investigation_id)
    deadline = time.monotonic() + timeout_seconds

    _enqueue_http_task(
        client=tasks_client,
        service_url=service_url,
        path=f"/runs/{run_id}/investigations",
        payload={
            "investigation_id": investigation_id,
            "spend_cap_usd": investigation_spend_cap_usd,
        },
        project=project,
        location=location,
        queue=queue,
        invoker_service_account=invoker_service_account,
    )

    investigation: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = investigation_ref.get()
        if snapshot.exists:
            investigation = snapshot.to_dict() or {}
            if investigation.get("status") == "branching" and investigation.get(
                "planned_branches"
            ):
                break
            if investigation.get("status") == "failed":
                raise RuntimeError(f"analysis failed: {investigation.get('error') or {}}")
        time.sleep(3)
    else:
        raise TimeoutError(f"timed out waiting for AnalystAgent: {investigation_id}")

    planned = sorted(investigation["planned_branches"], key=lambda item: item["rank"])
    if len(planned) != 3:
        raise RuntimeError(f"Analyst produced {len(planned)} branches instead of 3")
    for item in planned:
        _enqueue_http_task(
            client=tasks_client,
            service_url=service_url,
            path=f"/runs/{run_id}/forks",
            payload={
                "fork_seq": item["fork_seq"],
                "intervention": item["intervention"],
                "investigation_id": investigation_id,
                "branch_id": item["branch_id"],
                "branch_spend_cap_usd": branch_spend_cap_usd,
                "investigation_spend_cap_usd": investigation_spend_cap_usd,
            },
            project=project,
            location=location,
            queue=queue,
            invoker_service_account=invoker_service_account,
        )

    branch_collection = firestore_client.collection("runs").document(run_id).collection(
        "branches"
    )
    while time.monotonic() < deadline:
        branches = {}
        for item in planned:
            snapshot = branch_collection.document(item["branch_id"]).get()
            if snapshot.exists:
                branches[item["branch_id"]] = snapshot.to_dict() or {}
        failed = {
            branch_id: branch.get("error") or {}
            for branch_id, branch in branches.items()
            if branch.get("status") in {"failed", "aborted"}
        }
        if failed:
            raise RuntimeError(f"counterfactual branches failed: {failed}")
        if len(branches) == 3 and all(
            branch.get("status") == "completed" for branch in branches.values()
        ):
            break
        time.sleep(5)
    else:
        raise TimeoutError(f"timed out waiting for Firestore branch fan-in: {investigation_id}")

    _enqueue_http_task(
        client=tasks_client,
        service_url=service_url,
        path=f"/investigations/{investigation_id}/judge",
        payload={},
        project=project,
        location=location,
        queue=queue,
        invoker_service_account=invoker_service_account,
    )
    while time.monotonic() < deadline:
        snapshot = investigation_ref.get()
        if snapshot.exists:
            investigation = snapshot.to_dict() or {}
            if investigation.get("status") == "completed":
                return investigation
            if investigation.get("status") == "failed":
                raise RuntimeError(f"judging failed: {investigation.get('error') or {}}")
        time.sleep(3)
    raise TimeoutError(f"timed out waiting for JudgeAgent: {investigation_id}")


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


def _fork_via_tasks(
    *,
    service_url: str,
    run_id: str,
    fork_seq: int,
    intervention: dict[str, Any],
    investigation_id: str,
    branch_id: str,
    project: str,
    location: str,
    queue: str,
    bucket: str,
    invoker_service_account: str,
    branch_spend_cap_usd: float,
    investigation_spend_cap_usd: float,
) -> dict[str, Any]:
    credentials, _ = google.auth.default(quota_project_id=project)
    client = tasks_v2.CloudTasksClient(credentials=credentials)
    payload = {
        "fork_seq": fork_seq,
        "intervention": intervention,
        "investigation_id": investigation_id,
        "branch_id": branch_id,
        "branch_spend_cap_usd": branch_spend_cap_usd,
        "investigation_spend_cap_usd": investigation_spend_cap_usd,
    }
    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{service_url}/runs/{run_id}/forks",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode(),
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

    branch_ref = (
        firestore.Client(project=project, credentials=credentials)
        .collection("runs")
        .document(run_id)
        .collection("branches")
        .document(branch_id)
    )
    branch_blob = (
        storage.Client(project=project, credentials=credentials)
        .bucket(bucket)
        .blob(f"runs/{run_id}/artifacts/{branch_id}/branch.json")
    )
    deadline = time.monotonic() + 890
    while time.monotonic() < deadline:
        snapshot = branch_ref.get()
        if snapshot.exists:
            branch = snapshot.to_dict() or {}
            if branch.get("status") == "completed":
                return json.loads(branch_blob.download_as_text(timeout=30))
            if branch.get("status") in {"failed", "aborted"}:
                raise RuntimeError(f"branch failed: {branch.get('error') or {}}")
        time.sleep(5)
    raise TimeoutError(f"timed out waiting for branch: {branch_id}")


def _write_trace(trace: dict[str, Any], output: str | None, gcs_output: str | None) -> None:
    rendered = json.dumps(trace, indent=2, sort_keys=True, default=str) + "\n"
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
    parser = argparse.ArgumentParser(prog="culprit")
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

    fork_parser = subparsers.add_parser("fork", help="fork an existing run")
    fork_parser.add_argument("run_id")
    fork_parser.add_argument("fork_seq", type=int)
    fork_parser.add_argument("--investigation-id", required=True)
    fork_parser.add_argument("--branch-id")
    fork_parser.add_argument("--intervention-json", required=True)
    fork_parser.add_argument("--output")
    fork_parser.add_argument("--via-cloud-tasks", action="store_true")
    fork_parser.add_argument("--project", default="culprit-6f973")
    fork_parser.add_argument("--location", default="us-central1")
    fork_parser.add_argument("--queue", default="culprit-recordings")
    fork_parser.add_argument("--bucket")
    fork_parser.add_argument("--invoker-service-account")
    fork_parser.add_argument("--branch-spend-cap-usd", type=float, default=0.15)
    fork_parser.add_argument("--investigation-spend-cap-usd", type=float, default=0.45)

    investigate_parser = subparsers.add_parser(
        "investigate", help="autonomously investigate a failed run"
    )
    investigate_parser.add_argument("run_id")
    investigate_parser.add_argument("--investigation-id")
    investigate_parser.add_argument("--output")
    investigate_parser.add_argument("--project", default="culprit-6f973")
    investigate_parser.add_argument("--location", default="us-central1")
    investigate_parser.add_argument("--queue", default="culprit-recordings")
    investigate_parser.add_argument("--invoker-service-account")
    investigate_parser.add_argument("--branch-spend-cap-usd", type=float, default=0.15)
    investigate_parser.add_argument(
        "--investigation-spend-cap-usd",
        type=float,
        default=DEFAULT_AUTONOMOUS_INVESTIGATION_SPEND_CAP_USD,
    )
    investigate_parser.add_argument("--timeout-seconds", type=int, default=1_800)

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
    elif args.command == "trace":
        trace = _call("GET", f"{service_url}/runs/{args.run_id}/trace")
    elif args.command == "fork":
        try:
            intervention = json.loads(args.intervention_json)
        except json.JSONDecodeError as exc:
            parser.error(f"--intervention-json is invalid JSON: {exc}")
        branch_id = args.branch_id or (
            f"branch-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        )
        payload = {
            "fork_seq": args.fork_seq,
            "intervention": intervention,
            "investigation_id": args.investigation_id,
            "branch_id": branch_id,
            "branch_spend_cap_usd": args.branch_spend_cap_usd,
            "investigation_spend_cap_usd": args.investigation_spend_cap_usd,
        }
        if args.via_cloud_tasks:
            invoker = args.invoker_service_account or (
                f"culprit-runner@{args.project}.iam.gserviceaccount.com"
            )
            trace = _fork_via_tasks(
                service_url=service_url,
                run_id=args.run_id,
                fork_seq=args.fork_seq,
                intervention=intervention,
                investigation_id=args.investigation_id,
                branch_id=branch_id,
                project=args.project,
                location=args.location,
                queue=args.queue,
                bucket=args.bucket or f"{args.project}-state",
                invoker_service_account=invoker,
                branch_spend_cap_usd=args.branch_spend_cap_usd,
                investigation_spend_cap_usd=args.investigation_spend_cap_usd,
            )
        else:
            trace = _call("POST", f"{service_url}/runs/{args.run_id}/forks", payload=payload)
        print(f"run_id={trace['branch']['run_id']}")
        print(f"branch_id={trace['branch']['branch_id']}")
        _write_trace(trace, args.output, None)
        return
    else:
        investigation_id = args.investigation_id or (
            f"inv-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        )
        invoker = args.invoker_service_account or (
            f"culprit-runner@{args.project}.iam.gserviceaccount.com"
        )
        investigation = _investigate_via_tasks(
            service_url=service_url,
            run_id=args.run_id,
            investigation_id=investigation_id,
            project=args.project,
            location=args.location,
            queue=args.queue,
            invoker_service_account=invoker,
            branch_spend_cap_usd=args.branch_spend_cap_usd,
            investigation_spend_cap_usd=args.investigation_spend_cap_usd,
            timeout_seconds=args.timeout_seconds,
        )
        ranking = investigation["ranking"]
        print(f"run_id={args.run_id}")
        print(f"investigation_id={investigation_id}")
        print(f"top_culprit_event={ranking['candidates'][0]['event_seq']}")
        print(f"winner={investigation['winner']}")
        print(f"adk_eval_accepted={investigation['evalset_validation']['accepted']}")
        _write_trace(investigation, args.output, None)
        return
    print(f"run_id={trace['run']['run_id']}")
    _write_trace(trace, args.output, args.gcs_output)


if __name__ == "__main__":
    main()
