"""Firestore/GCS persistence for ordered P1 recordings."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from culprit_core.models import Checkpoint, Effect, Event, Run
from google.cloud import firestore, storage


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8") + b"\n"


class RecordingStore:
    def __init__(self, *, project: str, bucket: str) -> None:
        self.project = project
        self.bucket_name = bucket
        self.firestore = firestore.AsyncClient(project=project)
        self.storage = storage.Client(project=project)

    def _run_ref(self, run_id: str):
        return self.firestore.collection("runs").document(run_id)

    async def write_run(self, run: Run) -> None:
        await self._run_ref(run.run_id).set(run.model_dump(mode="json"))

    async def write_event(self, event: Event) -> None:
        ref = self._run_ref(event.run_id).collection("events").document(f"{event.seq:06d}")
        await ref.set(event.model_dump(mode="json"))

    async def write_effect(self, effect: Effect) -> None:
        ref = self._run_ref(effect.run_id).collection("effects").document(f"{effect.seq:06d}")
        await ref.set(effect.model_dump(mode="json"))

    async def write_checkpoint(self, checkpoint: Checkpoint) -> None:
        ref = (
            self._run_ref(checkpoint.run_id)
            .collection("checkpoints")
            .document(checkpoint.checkpoint_id)
        )
        await ref.set(checkpoint.model_dump(mode="json"))

    async def upload_bytes(
        self,
        object_name: str,
        payload: bytes,
        *,
        content_type: str,
    ) -> str:
        blob = self.storage.bucket(self.bucket_name).blob(object_name)
        await asyncio.to_thread(
            blob.upload_from_string,
            payload,
            content_type=content_type,
            if_generation_match=0,
        )
        return f"gs://{self.bucket_name}/{object_name}"

    async def query_trace(self, run_id: str) -> dict[str, Any]:
        run_snapshot = await self._run_ref(run_id).get()
        if not run_snapshot.exists:
            raise KeyError(run_id)

        async def ordered_subcollection(name: str, field: str) -> list[dict[str, Any]]:
            query = (
                self._run_ref(run_id)
                .collection(name)
                .order_by(field, direction=firestore.Query.ASCENDING)
            )
            return [snapshot.to_dict() async for snapshot in query.stream()]

        events, effects, checkpoints = await asyncio.gather(
            ordered_subcollection("events", "seq"),
            ordered_subcollection("effects", "seq"),
            ordered_subcollection("checkpoints", "parent_seq"),
        )
        event_sequences = [item["seq"] for item in events]
        effect_sequences = [item["seq"] for item in effects]
        return {
            "run": run_snapshot.to_dict(),
            "events": events,
            "effects": effects,
            "checkpoints": checkpoints,
            "verification": {
                "events_queryable_and_ordered": event_sequences == list(range(len(events))),
                "effects_queryable_and_ordered": effect_sequences == list(range(len(effects))),
            },
        }

    async def upload_trace(self, run_id: str, trace: dict[str, Any]) -> str:
        await self.upload_bytes(
            f"runs/{run_id}/artifacts/events.jsonl",
            b"".join(json_bytes(event) for event in trace["events"]),
            content_type="application/x-ndjson",
        )
        return await self.upload_bytes(
            f"runs/{run_id}/artifacts/trace.json",
            json_bytes(trace),
            content_type="application/json",
        )

    async def download_gcs_uri(self, uri: str) -> bytes:
        prefix = f"gs://{self.bucket_name}/"
        if not uri.startswith(prefix):
            raise ValueError(f"object is outside configured bucket: {uri}")
        blob = self.storage.bucket(self.bucket_name).blob(uri.removeprefix(prefix))
        return await asyncio.to_thread(blob.download_as_bytes)
