from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from google.cloud import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter


def _sorted_documents(documents, field: str) -> list[dict[str, Any]]:
    values = [document.to_dict() or {} for document in documents]

    def key(item: dict[str, Any]) -> tuple[int, float | str]:
        value = item.get(field, "")
        if isinstance(value, int | float):
            return 0, float(value)
        return 1, str(value)

    return sorted(values, key=key)


class ControlStore:
    """Read-only UI access plus narrow orchestration-state reads."""

    def __init__(self, *, project_id: str, bucket_name: str) -> None:
        self.project_id = project_id
        self.bucket_name = bucket_name
        self.firestore = firestore.Client(project=project_id)
        self.storage = storage.Client(project=project_id)

    def _run_ref(self, run_id: str):
        return self.firestore.collection("runs").document(run_id)

    def archive_run(self, run_id: str) -> None:
        self._run_ref(run_id).set(
            {"archived": True, "archived_at": datetime.now(UTC).isoformat()},
            merge=True,
        )

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        documents = (
            self.firestore.collection("runs")
            .order_by("started_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        items = [document.to_dict() or {} for document in documents]
        return [item for item in items if not item.get("archived")]

    def get_run_document(self, run_id: str) -> dict[str, Any]:
        snapshot = self._run_ref(run_id).get()
        if not snapshot.exists:
            raise KeyError(run_id)
        return snapshot.to_dict() or {}

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        run = self.get_run_document(run_id)
        run_ref = self._run_ref(run_id)
        return {
            "run": run,
            "events": _sorted_documents(run_ref.collection("events").stream(), "seq"),
            "effects": _sorted_documents(run_ref.collection("effects").stream(), "seq"),
            "checkpoints": _sorted_documents(
                run_ref.collection("checkpoints").stream(), "parent_seq"
            ),
            "grades": _sorted_documents(run_ref.collection("grades").stream(), "criterion_id"),
        }

    def get_branch_detail(self, run_id: str, branch_id: str) -> dict[str, Any]:
        branch_ref = self._run_ref(run_id).collection("branches").document(branch_id)
        snapshot = branch_ref.get()
        if not snapshot.exists:
            return {
                "branch": {"branch_id": branch_id, "status": "queued"},
                "events": [],
                "effects": [],
                "grades": [],
            }
        return {
            "branch": snapshot.to_dict() or {},
            "events": _sorted_documents(branch_ref.collection("events").stream(), "seq"),
            "effects": _sorted_documents(branch_ref.collection("effects").stream(), "seq"),
            "grades": _sorted_documents(branch_ref.collection("grades").stream(), "criterion_id"),
        }

    def get_investigation_document(self, investigation_id: str) -> dict[str, Any]:
        snapshot = self.firestore.collection("investigations").document(investigation_id).get()
        if not snapshot.exists:
            raise KeyError(investigation_id)
        return snapshot.to_dict() or {}

    def get_investigation(self, investigation_id: str) -> dict[str, Any]:
        investigation = self.get_investigation_document(investigation_id)
        run_id = str(investigation["run_id"])
        branches = [
            self.get_branch_detail(run_id, str(branch_id))
            for branch_id in investigation.get("branch_ids", [])
        ]
        return {"investigation": investigation, "branches": branches}

    def merge_investigation(self, investigation_id: str, update: dict[str, Any]) -> None:
        self.firestore.collection("investigations").document(investigation_id).set(
            update, merge=True
        )

    def investigations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        query = self.firestore.collection("investigations").where(
            filter=FieldFilter("run_id", "==", run_id)
        )
        documents = [snapshot.to_dict() or {} for snapshot in query.stream()]
        return sorted(
            documents,
            key=lambda item: str(item.get("started_at") or item.get("updated_at") or ""),
            reverse=True,
        )

    def latest_investigation(self, run_id: str, *, preferred_id: str = "") -> dict[str, Any] | None:
        if preferred_id:
            try:
                preferred = self.get_investigation(preferred_id)
                if preferred["investigation"].get("run_id") == run_id:
                    return preferred
            except KeyError:
                pass
        investigations = self.investigations_for_run(run_id)
        if not investigations:
            return None
        return self.get_investigation(str(investigations[0]["investigation_id"]))

    def get_evalset(self, evalset_id: str) -> tuple[dict[str, Any], bytes]:
        snapshot = self.firestore.collection("evalsets").document(evalset_id).get()
        if not snapshot.exists:
            raise KeyError(evalset_id)
        document = snapshot.to_dict() or {}
        uri = str(document.get("gcs_uri", ""))
        prefix = f"gs://{self.bucket_name}/"
        if not uri.startswith(prefix):
            raise ValueError("evalset points outside the configured Culprit bucket")
        payload = (
            self.storage.bucket(self.bucket_name).blob(uri.removeprefix(prefix)).download_as_bytes()
        )
        return document, payload

    def stream_state(self, run_id: str) -> tuple[str, dict[str, Any]]:
        try:
            run = self.get_run_document(run_id)
        except KeyError:
            state = {"run_id": run_id, "exists": False}
            return hashlib.sha256(json.dumps(state).encode()).hexdigest(), state
        run_ref = self._run_ref(run_id)
        events = _sorted_documents(run_ref.collection("events").stream(), "seq")
        effects = _sorted_documents(run_ref.collection("effects").stream(), "seq")
        grades = _sorted_documents(run_ref.collection("grades").stream(), "criterion_id")
        investigations = self.investigations_for_run(run_id)
        latest = investigations[0] if investigations else {}
        branch_states: list[dict[str, Any]] = []
        for branch_id in latest.get("branch_ids", []):
            snapshot = run_ref.collection("branches").document(str(branch_id)).get()
            branch = snapshot.to_dict() or {} if snapshot.exists else {}
            branch_states.append(
                {
                    "branch_id": branch_id,
                    "status": branch.get("status", "queued"),
                    "verdict": branch.get("verdict"),
                    "event_count": branch.get("event_count", 0),
                    "effect_count": branch.get("effect_count", 0),
                    "completed_at": branch.get("completed_at"),
                }
            )
        state = {
            "run_id": run_id,
            "exists": True,
            "status": run.get("status"),
            "verdict": run.get("verdict"),
            "event_sequences": [event.get("seq") for event in events],
            "effect_sequences": [effect.get("seq") for effect in effects],
            "grade_ids": [grade.get("criterion_id") for grade in grades],
            "investigation": {
                "investigation_id": latest.get("investigation_id"),
                "status": latest.get("status"),
                "updated_at": latest.get("updated_at"),
                "winner": latest.get("winner"),
            },
            "branches": branch_states,
        }
        encoded = json.dumps(state, sort_keys=True, default=str, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest(), state
