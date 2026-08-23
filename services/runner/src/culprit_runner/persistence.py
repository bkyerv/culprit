"""Firestore/GCS persistence for ordered P1 recordings."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from culprit_core.models import Branch, Checkpoint, Effect, Event, Grade, Run
from google.cloud import firestore, storage

MAX_BRANCHES_PER_INVESTIGATION = 3


class BranchLimitExceeded(RuntimeError):
    pass


class InvestigationSpendExceeded(RuntimeError):
    pass


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8") + b"\n"


def json_line_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        + b"\n"
    )


class RecordingStore:
    def __init__(self, *, project: str, bucket: str) -> None:
        self.project = project
        self.bucket_name = bucket
        self.firestore = firestore.AsyncClient(project=project)
        self.storage = storage.Client(project=project)

    def _run_ref(self, run_id: str):
        return self.firestore.collection("runs").document(run_id)

    def _branch_ref(self, run_id: str, branch_id: str):
        return self._run_ref(run_id).collection("branches").document(branch_id)

    def _investigation_ref(self, investigation_id: str):
        return self.firestore.collection("investigations").document(investigation_id)

    async def write_run(self, run: Run) -> None:
        await self._run_ref(run.run_id).set(run.model_dump(mode="json"))

    async def write_event(self, event: Event) -> None:
        ref = self._run_ref(event.run_id).collection("events").document(f"{event.seq:06d}")
        await ref.set(event.model_dump(mode="json"))

    async def write_effect(self, effect: Effect) -> None:
        ref = self._run_ref(effect.run_id).collection("effects").document(f"{effect.seq:06d}")
        await ref.set(effect.model_dump(mode="json"))

    async def write_branch_event(self, event: Event) -> None:
        if not event.branch_id:
            raise ValueError("branch event requires branch_id")
        ref = (
            self._branch_ref(event.run_id, event.branch_id)
            .collection("events")
            .document(f"{event.seq:06d}")
        )
        await ref.set(event.model_dump(mode="json"))

    async def write_branch_effect(self, effect: Effect) -> None:
        if not effect.branch_id:
            raise ValueError("branch effect requires branch_id")
        ref = (
            self._branch_ref(effect.run_id, effect.branch_id)
            .collection("effects")
            .document(f"{effect.seq:06d}")
        )
        await ref.set(effect.model_dump(mode="json"))

    async def write_checkpoint(self, checkpoint: Checkpoint) -> None:
        ref = (
            self._run_ref(checkpoint.run_id)
            .collection("checkpoints")
            .document(checkpoint.checkpoint_id)
        )
        await ref.set(checkpoint.model_dump(mode="json"))

    async def write_grade(self, grade: Grade) -> None:
        if grade.branch_id:
            ref = (
                self._branch_ref(grade.run_id, grade.branch_id)
                .collection("grades")
                .document(grade.criterion_id)
            )
        else:
            ref = self._run_ref(grade.run_id).collection("grades").document(grade.criterion_id)
        await ref.set(grade.model_dump(mode="json"))

    async def allocate_branch(self, branch: Branch) -> bool:
        """Atomically reserve one of three branch slots and its spend budget."""

        transaction = self.firestore.transaction()
        investigation_ref = self._investigation_ref(branch.investigation_id)
        branch_ref = self._branch_ref(branch.run_id, branch.branch_id)

        @firestore.async_transactional
        async def allocate(transaction):
            existing_branch = await branch_ref.get(transaction=transaction)
            if existing_branch.exists:
                stored = existing_branch.to_dict() or {}
                if stored.get("fork_seq") != branch.fork_seq or stored.get(
                    "intervention"
                ) != branch.intervention.model_dump(mode="json"):
                    raise ValueError(f"branch id already has a different spec: {branch.branch_id}")
                return False

            snapshot = await investigation_ref.get(transaction=transaction)
            if snapshot.exists:
                investigation = snapshot.to_dict() or {}
                if investigation.get("run_id") != branch.run_id:
                    raise ValueError("investigation cannot span multiple runs")
                branch_ids = list(investigation.get("branch_ids", []))
                if len(branch_ids) >= MAX_BRANCHES_PER_INVESTIGATION:
                    raise BranchLimitExceeded(
                        f"investigation {branch.investigation_id} already has "
                        f"{MAX_BRANCHES_PER_INVESTIGATION} branches"
                    )
                spend_cap = float(investigation["spend_cap_usd"])
                if abs(spend_cap - branch.investigation_spend_cap_usd) > 1e-9:
                    raise ValueError("investigation spend cap cannot change after allocation")
                accounted = float(investigation.get("accounted_spend_usd", 0))
                committed = float(investigation.get("committed_spend_usd", 0))
            else:
                branch_ids = []
                spend_cap = branch.investigation_spend_cap_usd
                accounted = 0.0
                committed = 0.0

            if accounted + committed + branch.branch_spend_cap_usd > spend_cap + 1e-9:
                raise InvestigationSpendExceeded(
                    f"investigation spend cap ${spend_cap:.6f} would be exceeded"
                )
            branch_ids.append(branch.branch_id)
            investigation_update = {
                "branch_ids": branch_ids,
                "max_branches": MAX_BRANCHES_PER_INVESTIGATION,
                "spend_cap_usd": spend_cap,
                "accounted_spend_usd": accounted,
                "committed_spend_usd": round(committed + branch.branch_spend_cap_usd, 9),
                "updated_at": branch.started_at,
            }
            if snapshot.exists:
                # Preserve the Analyst ranking and failure confirmation written by
                # P3. The P2 allocator used to replace the whole investigation.
                transaction.update(investigation_ref, investigation_update)
            else:
                transaction.set(
                    investigation_ref,
                    {
                        "investigation_id": branch.investigation_id,
                        "run_id": branch.run_id,
                        "status": "running",
                        **investigation_update,
                    },
                )
            transaction.set(branch_ref, branch.model_dump(mode="json"))
            return True

        return bool(await allocate(transaction))

    async def write_branch(self, branch: Branch) -> None:
        await self._branch_ref(branch.run_id, branch.branch_id).set(branch.model_dump(mode="json"))

    async def finalize_branch_spend(self, branch: Branch) -> None:
        transaction = self.firestore.transaction()
        investigation_ref = self._investigation_ref(branch.investigation_id)

        @firestore.async_transactional
        async def finalize(transaction):
            snapshot = await investigation_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(branch.investigation_id)
            investigation = snapshot.to_dict() or {}
            finalized = list(investigation.get("spend_finalized_branch_ids", []))
            if branch.branch_id in finalized:
                return
            committed = max(
                0.0,
                float(investigation.get("committed_spend_usd", 0)) - branch.branch_spend_cap_usd,
            )
            accounted = round(
                float(investigation.get("accounted_spend_usd", 0)) + branch.accounted_spend_usd,
                9,
            )
            finalized.append(branch.branch_id)
            terminal = list(investigation.get("terminal_branch_ids", []))
            if branch.branch_id not in terminal:
                terminal.append(branch.branch_id)
            branch_ids = list(investigation.get("branch_ids", []))
            expected = int(
                investigation.get("expected_branch_count", MAX_BRANCHES_PER_INVESTIGATION)
            )
            update = {
                "committed_spend_usd": round(committed, 9),
                "accounted_spend_usd": accounted,
                "spend_finalized_branch_ids": finalized,
                "terminal_branch_ids": terminal,
                "updated_at": branch.completed_at or branch.started_at,
            }
            if len(branch_ids) == expected and set(terminal) == set(branch_ids):
                update["status"] = "awaiting_judge"
            transaction.update(investigation_ref, update)

        await finalize(transaction)

    async def begin_investigation(
        self,
        *,
        investigation_id: str,
        run_id: str,
        spend_cap_usd: float,
        criteria_fingerprint: str,
        started_at: Any,
    ) -> dict[str, Any]:
        """Create the P3 fan-out record once, preserving task retry idempotence."""

        transaction = self.firestore.transaction()
        ref = self._investigation_ref(investigation_id)

        @firestore.async_transactional
        async def begin(transaction):
            snapshot = await ref.get(transaction=transaction)
            if snapshot.exists:
                existing = snapshot.to_dict() or {}
                if existing.get("run_id") != run_id:
                    raise ValueError("investigation cannot span multiple runs")
                if abs(float(existing.get("spend_cap_usd", 0)) - spend_cap_usd) > 1e-9:
                    raise ValueError("investigation spend cap cannot change")
                return existing
            document = {
                "investigation_id": investigation_id,
                "run_id": run_id,
                "status": "analysis_running",
                "criteria_fingerprint": criteria_fingerprint,
                "branch_ids": [],
                "terminal_branch_ids": [],
                "spend_finalized_branch_ids": [],
                "expected_branch_count": MAX_BRANCHES_PER_INVESTIGATION,
                "max_branches": MAX_BRANCHES_PER_INVESTIGATION,
                "spend_cap_usd": spend_cap_usd,
                "accounted_spend_usd": 0.0,
                "committed_spend_usd": 0.0,
                "stage_spend_usd": {},
                "started_at": started_at,
                "updated_at": started_at,
            }
            transaction.set(ref, document)
            return document

        return dict(await begin(transaction))

    async def complete_investigation_stage(
        self,
        *,
        investigation_id: str,
        stage: str,
        spend_usd: float,
        update: dict[str, Any],
    ) -> None:
        """Atomically account an Analyst/Judge call and persist its output."""

        transaction = self.firestore.transaction()
        ref = self._investigation_ref(investigation_id)

        @firestore.async_transactional
        async def complete(transaction):
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(investigation_id)
            document = snapshot.to_dict() or {}
            stage_spend = dict(document.get("stage_spend_usd", {}))
            already_accounted = float(stage_spend.get(stage, 0))
            delta = max(0.0, spend_usd - already_accounted)
            accounted = round(float(document.get("accounted_spend_usd", 0)) + delta, 9)
            committed = float(document.get("committed_spend_usd", 0))
            cap = float(document["spend_cap_usd"])
            if accounted + committed > cap + 1e-9:
                raise InvestigationSpendExceeded(
                    f"investigation spend cap ${cap:.6f} exceeded during {stage}"
                )
            stage_spend[stage] = round(max(spend_usd, already_accounted), 9)
            transaction.update(
                ref,
                {
                    **update,
                    "stage_spend_usd": stage_spend,
                    "accounted_spend_usd": accounted,
                },
            )

        await complete(transaction)

    async def fail_investigation(
        self, investigation_id: str, *, error: dict[str, Any], updated_at: Any
    ) -> None:
        await self._investigation_ref(investigation_id).set(
            {"status": "failed", "error": error, "updated_at": updated_at}, merge=True
        )

    async def query_investigation(self, investigation_id: str) -> dict[str, Any]:
        snapshot = await self._investigation_ref(investigation_id).get()
        if not snapshot.exists:
            raise KeyError(investigation_id)
        return dict(snapshot.to_dict() or {})

    async def update_investigation(
        self, investigation_id: str, update: dict[str, Any]
    ) -> None:
        await self._investigation_ref(investigation_id).set(update, merge=True)

    async def write_evalset(self, evalset_id: str, document: dict[str, Any]) -> None:
        await self.firestore.collection("evalsets").document(evalset_id).set(document)

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

        events, effects, checkpoints, grades = await asyncio.gather(
            ordered_subcollection("events", "seq"),
            ordered_subcollection("effects", "seq"),
            ordered_subcollection("checkpoints", "parent_seq"),
            ordered_subcollection("grades", "criterion_id"),
        )
        event_sequences = [item["seq"] for item in events]
        effect_sequences = [item["seq"] for item in effects]
        return {
            "run": run_snapshot.to_dict(),
            "events": events,
            "effects": effects,
            "checkpoints": checkpoints,
            "grades": grades,
            "verification": {
                "events_queryable_and_ordered": event_sequences == list(range(len(events))),
                "effects_queryable_and_ordered": effect_sequences == list(range(len(effects))),
            },
        }

    async def query_branch(self, run_id: str, branch_id: str) -> dict[str, Any]:
        branch_ref = self._branch_ref(run_id, branch_id)
        snapshot = await branch_ref.get()
        if not snapshot.exists:
            raise KeyError(branch_id)

        async def ordered(name: str, field: str) -> list[dict[str, Any]]:
            query = branch_ref.collection(name).order_by(field, direction=firestore.Query.ASCENDING)
            return [item.to_dict() async for item in query.stream()]

        events, effects, grades = await asyncio.gather(
            ordered("events", "seq"),
            ordered("effects", "seq"),
            ordered("grades", "criterion_id"),
        )
        return {
            "branch": snapshot.to_dict(),
            "events": events,
            "effects": effects,
            "grades": grades,
            "verification": {
                "events_have_capabilities": all(
                    bool(event.get("capability_set")) for event in events
                ),
                "events_tagged_with_branch_id": all(
                    event.get("branch_id") == branch_id for event in events
                ),
                "effects_tagged_with_branch_id": all(
                    effect.get("branch_id") == branch_id for effect in effects
                ),
                "novel_effect_count": sum(bool(effect.get("novel")) for effect in effects),
            },
        }

    async def upload_trace(self, run_id: str, trace: dict[str, Any]) -> str:
        await self.upload_bytes(
            f"runs/{run_id}/artifacts/events.jsonl",
            b"".join(json_line_bytes(event) for event in trace["events"]),
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
