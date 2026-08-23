"""P2 counterfactual branch execution from recorded world state."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zstandard
from culprit_core import (
    Branch,
    CapabilitySet,
    Checkpoint,
    Effect,
    EffectMode,
    Event,
    Run,
    TokenUsage,
)
from culprit_core.models import EffectOutcome, Intervention, canonical_sha256
from google.adk.agents import Agent
from google.adk.events import Event as AdkEvent
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from culprit_runner.adjudicator import Adjudicator
from culprit_runner.effect_broker import EffectBroker, GeminiWorldModel
from culprit_runner.interventions import (
    apply_effect_outcome_to_ledger,
    apply_intervention,
    branch_effect_prefix,
    capability_is_subset,
    collect_function_calls,
)
from culprit_runner.persistence import (
    MAX_BRANCHES_PER_INVESTIGATION,
    RecordingStore,
    json_bytes,
    json_line_bytes,
)
from culprit_runner.recorder import (
    MODEL,
    SUBJECT_INSTRUCTION,
    _cost_usd,
    _event_kind,
    _jsonable_adk_event,
    _text_from_event,
    _token_usage,
)
from culprit_runner.sandbox_driver import SandboxDriver
from culprit_runner.tools import create_tool_surface

APP_NAME = "culprit_branch"
# Leave one full 120-second command budget beneath the 15-minute hard ceiling,
# so a synchronous launcher command cannot carry cancellation past the limit.
BRANCH_WALL_CLOCK_SECONDS = 13 * 60
SANDBOX_LIFETIME_SECONDS = 15 * 60
DEFAULT_BRANCH_SPEND_CAP_USD = 0.15
DEFAULT_INVESTIGATION_SPEND_CAP_USD = 0.45
GRADING_SPEND_RESERVATION_USD = 0.05
CONTINUATION_MESSAGE = (
    "Continue the original task from the reconstructed history. Treat the function results "
    "above as authoritative for this counterfactual, complete all remaining work now, and do "
    "not repeat any outward effect already present in the history."
)
SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,127}$")


class BranchWallClockExceeded(TimeoutError):
    pass


class BranchModelSpendExceeded(RuntimeError):
    pass


def _new_branch_id() -> str:
    return f"branch-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _validate_id(value: str, *, label: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must contain only letters, digits, and dashes")
    return value


def _select_checkpoint(checkpoints: list[Checkpoint], fork_seq: int) -> Checkpoint:
    eligible = [checkpoint for checkpoint in checkpoints if checkpoint.parent_seq <= fork_seq]
    if not eligible:
        raise ValueError(f"run has no checkpoint at or before event {fork_seq}")
    return max(eligible, key=lambda checkpoint: checkpoint.parent_seq)


def _branch_event(
    *,
    run_id: str,
    branch_id: str,
    seq: int,
    adk_event: AdkEvent,
    capabilities: CapabilitySet,
    phase: str,
    source_event_seq: int | None,
    token_usage: TokenUsage | None = None,
    latency_ms: float = 0,
    cost_usd: float = 0,
) -> Event:
    content = getattr(adk_event, "content", None)
    kind, _ = _event_kind(adk_event)
    if getattr(adk_event, "author", None) == "user" and not adk_event.get_function_responses():
        kind = "user_message"
    return Event(
        run_id=run_id,
        branch_id=branch_id,
        seq=seq,
        event_id=str(adk_event.id or uuid.uuid4().hex),
        role=(getattr(content, "role", None) or getattr(adk_event, "author", None) or "system"),
        kind=kind,
        payload=_jsonable_adk_event(adk_event),
        token_usage=token_usage or TokenUsage(),
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        capability_set=capabilities.model_copy(deep=True),
        phase=phase,
        source_event_seq=source_event_seq,
    )


class BranchService:
    def __init__(
        self,
        *,
        project: str,
        bucket: str,
        location: str = "global",
        driver: SandboxDriver | None = None,
        store: RecordingStore | None = None,
    ) -> None:
        if location != "global":
            raise ValueError("Gemini 3.x must use the global Vertex AI location")
        self.project = project
        self.bucket = bucket
        self.location = location
        self.driver = driver or SandboxDriver()
        self.store = store or RecordingStore(project=project, bucket=bucket)

    def _build_agent(self, *, capabilities: CapabilitySet, surface, patch: str | None) -> Agent:
        instruction = SUBJECT_INSTRUCTION
        if patch:
            instruction = f"{instruction}\n\nCounterfactual instruction patch:\n{patch}"
        return Agent(
            # Keep the recorded author name so ADK treats the reconstructed
            # events as this agent's own history rather than another agent's.
            name="subject_agent",
            description="BranchAgent continuing a counterfactual SubjectAgent session.",
            model=Gemini(
                model=MODEL,
                retry_options=types.HttpRetryOptions(
                    attempts=4,
                    initial_delay=1,
                    max_delay=12,
                    exp_base=2,
                    http_status_codes=[429, 500, 502, 503, 504],
                ),
            ),
            instruction=instruction,
            tools=surface.functions,
            generate_content_config=types.GenerateContentConfig(
                temperature=0.35,
                max_output_tokens=4096,
            ),
        )

    async def fork_run(
        self,
        *,
        run_id: str,
        fork_seq: int,
        intervention: Intervention,
        investigation_id: str,
        branch_id: str | None = None,
        branch_spend_cap_usd: float = DEFAULT_BRANCH_SPEND_CAP_USD,
        investigation_spend_cap_usd: float = DEFAULT_INVESTIGATION_SPEND_CAP_USD,
    ) -> dict[str, Any]:
        _validate_id(run_id, label="run_id")
        _validate_id(investigation_id, label="investigation_id")
        branch_id = _validate_id(branch_id or _new_branch_id(), label="branch_id")
        if branch_spend_cap_usd <= GRADING_SPEND_RESERVATION_USD:
            raise ValueError("branch spend cap must exceed the grading reservation")
        if investigation_spend_cap_usd < branch_spend_cap_usd:
            raise ValueError("investigation spend cap must cover at least one branch")

        trace = await self.store.query_trace(run_id)
        run = Run.model_validate(trace["run"])
        if run.status != "completed":
            raise ValueError(f"only completed runs can be forked, got {run.status}")
        if fork_seq >= len(trace["events"]):
            raise ValueError(f"fork_seq {fork_seq} is outside recorded event range")

        original_events = [Event.model_validate(item) for item in trace["events"]]
        adk_events = [AdkEvent.model_validate(item.payload) for item in original_events]
        calls_by_id = collect_function_calls(adk_events[: fork_seq + 1])
        baseline_capabilities = original_events[fork_seq].capability_set
        applied = apply_intervention(
            event=adk_events[fork_seq],
            intervention=intervention,
            original_capabilities=baseline_capabilities,
            calls_by_id=calls_by_id,
        )
        if not capability_is_subset(applied.capabilities, baseline_capabilities):
            raise ValueError("branch effective capabilities exceed the original event authority")
        adk_events[fork_seq] = applied.event

        checkpoints = [Checkpoint.model_validate(item) for item in trace["checkpoints"]]
        checkpoint = _select_checkpoint(checkpoints, fork_seq)
        branch = Branch(
            run_id=run_id,
            branch_id=branch_id,
            investigation_id=investigation_id,
            fork_seq=fork_seq,
            intervention=intervention,
            source_checkpoint_id=checkpoint.checkpoint_id,
            source_checkpoint_parent_seq=checkpoint.parent_seq,
            source_checkpoint_gcs_uri=checkpoint.workspace_gcs_uri,
            source_ledger_len=checkpoint.ledger_len,
            original_capabilities=baseline_capabilities,
            effective_capabilities=applied.capabilities,
            capability_derivations=list(applied.detail.get("capability_derivations", [])),
            branch_spend_cap_usd=branch_spend_cap_usd,
            investigation_spend_cap_usd=investigation_spend_cap_usd,
            grading_spend_reservation_usd=GRADING_SPEND_RESERVATION_USD,
            applied_intervention=applied.detail,
        )
        allocated = await self.store.allocate_branch(branch)
        if not allocated:
            return await self.store.query_branch(run_id, branch_id)

        started = time.perf_counter()
        try:
            if not self.driver.available():
                raise RuntimeError(
                    "the Cloud Run sandbox binary is unavailable; P2 cannot run locally"
                )
            async with asyncio.timeout(BRANCH_WALL_CLOCK_SECONDS):
                result = await self._execute_branch(
                    branch=branch,
                    run=run,
                    trace=trace,
                    original_events=original_events,
                    adk_events=adk_events[: fork_seq + 1],
                    calls_by_id=calls_by_id,
                    checkpoint=checkpoint,
                    instruction_patch=applied.instruction_patch,
                    started=started,
                )
            return result
        except TimeoutError as exc:
            branch.status = "aborted"
            branch.error = {
                "type": "BranchWallClockExceeded",
                "message": f"branch exceeded {BRANCH_WALL_CLOCK_SECONDS} seconds",
            }
            raise BranchWallClockExceeded(branch.error["message"]) from exc
        except BranchModelSpendExceeded:
            branch.status = "aborted"
            raise
        except Exception as exc:
            branch.status = "failed"
            branch.error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            if branch.status not in {"completed"}:
                branch.completed_at = datetime.now(UTC)
                branch.duration_ms = round((time.perf_counter() - started) * 1000, 3)
                branch.accounted_spend_usd = branch.model_spend_usd
                await self.store.write_branch(branch)
            await self.store.finalize_branch_spend(branch)

    async def _execute_branch(
        self,
        *,
        branch: Branch,
        run: Run,
        trace: dict[str, Any],
        original_events: list[Event],
        adk_events: list[AdkEvent],
        calls_by_id: dict[str, dict[str, Any]],
        checkpoint: Checkpoint,
        instruction_patch: str | None,
        started: float,
    ) -> dict[str, Any]:
        sandbox_name = f"p2-{uuid.uuid4().hex[:16]}"
        runner: Runner | None = None
        sandbox_started = False
        branch_events: list[Event] = []
        grades = []
        with tempfile.TemporaryDirectory(prefix=f"culprit-{branch.branch_id}-") as temp_name:
            temp_dir = Path(temp_name)
            try:
                compressed = await self.store.download_gcs_uri(checkpoint.workspace_gcs_uri)
                if len(compressed) != checkpoint.bytes:
                    raise ValueError("source checkpoint byte count does not match Firestore")
                if hashlib.sha256(compressed).hexdigest() != checkpoint.sha256:
                    raise ValueError("source checkpoint SHA-256 does not match Firestore")
                raw = zstandard.ZstdDecompressor().decompress(
                    compressed, max_output_size=checkpoint.uncompressed_bytes
                )
                import_tar = temp_dir / "checkpoint.tar"
                import_tar.write_bytes(raw)
                self.driver.start(
                    sandbox_name,
                    import_tar=import_tar,
                    lifetime=SANDBOX_LIFETIME_SECONDS,
                )
                sandbox_started = True
                branch.status = "running"
                await self.store.write_branch(branch)

                original_effect_dicts = trace["effects"][: checkpoint.ledger_len]
                if canonical_sha256(original_effect_dicts) != checkpoint.effect_ledger_sha256:
                    raise ValueError("checkpoint effect ledger SHA-256 does not match")
                recorded_effects = [Effect.model_validate(item) for item in trace["effects"]]
                inherited = branch_effect_prefix(
                    recorded_effects,
                    branch_id=branch.branch_id,
                    ledger_len=checkpoint.ledger_len,
                )
                if isinstance(branch.intervention, EffectOutcome):
                    changed_effect_seq = apply_effect_outcome_to_ledger(
                        inherited,
                        intervention=branch.intervention,
                        calls_by_id=calls_by_id,
                    )
                    branch.applied_intervention["rewritten_effect_seq"] = changed_effect_seq
                for effect in inherited:
                    await self.store.write_branch_effect(effect)

                world_model = GeminiWorldModel(
                    project=self.project, location=self.location, model=MODEL
                )
                broker = EffectBroker(
                    run_id=run.run_id,
                    branch_id=branch.branch_id,
                    mode=EffectMode.REPLAY,
                    world_model=world_model,
                    effect_sink=self.store.write_branch_effect,
                    replay_history=inherited,
                    initial_ledger=inherited,
                )
                surface = create_tool_surface(
                    driver=self.driver,
                    sandbox_name=sandbox_name,
                    capabilities=branch.effective_capabilities,
                    broker=broker,
                    user_answers={},
                )
                agent = self._build_agent(
                    capabilities=branch.effective_capabilities,
                    surface=surface,
                    patch=instruction_patch,
                )
                session_service = InMemorySessionService()
                session_id = f"session-{branch.branch_id}"
                session = await session_service.create_session(
                    app_name=APP_NAME,
                    user_id="scenario",
                    session_id=session_id,
                )

                first = adk_events[0]
                bootstrap = AdkEvent(
                    id=f"bootstrap-{branch.branch_id}",
                    invocation_id=first.invocation_id,
                    author="user",
                    content=types.Content(role="user", parts=[types.Part.from_text(text=run.task)]),
                    timestamp=max(0.0, float(first.timestamp) - 0.001),
                )
                await session_service.append_event(session=session, event=bootstrap)
                bootstrap_event = _branch_event(
                    run_id=run.run_id,
                    branch_id=branch.branch_id,
                    seq=0,
                    adk_event=bootstrap,
                    capabilities=original_events[0].capability_set,
                    phase="replay",
                    source_event_seq=None,
                )
                branch_events.append(bootstrap_event)
                await self.store.write_branch_event(bootstrap_event)

                for source_seq, adk_event in enumerate(adk_events):
                    await session_service.append_event(session=session, event=adk_event)
                    capabilities = (
                        branch.effective_capabilities
                        if source_seq == branch.fork_seq
                        else original_events[source_seq].capability_set
                    )
                    replay_event = _branch_event(
                        run_id=run.run_id,
                        branch_id=branch.branch_id,
                        seq=len(branch_events),
                        adk_event=adk_event,
                        capabilities=capabilities,
                        phase="replay",
                        source_event_seq=source_seq,
                    )
                    branch_events.append(replay_event)
                    await self.store.write_branch_event(replay_event)

                reconstructed_event_count = len(session.events)
                runner = Runner(
                    app_name=APP_NAME,
                    agent=agent,
                    session_service=session_service,
                )
                yielded_metadata: dict[str, tuple[TokenUsage, float, float]] = {}
                last_yield = time.perf_counter()
                continuation = types.Content(
                    role="user", parts=[types.Part.from_text(text=CONTINUATION_MESSAGE)]
                )
                async for adk_event in runner.run_async(
                    user_id="scenario",
                    session_id=session_id,
                    new_message=continuation,
                ):
                    now = time.perf_counter()
                    usage = _token_usage(adk_event)
                    yielded_metadata[str(adk_event.id)] = (
                        usage,
                        round((now - last_yield) * 1000, 3),
                        _cost_usd(usage),
                    )
                    last_yield = now
                    measured = sum(item[2] for item in yielded_metadata.values()) + sum(
                        float(effect.response.get("world_model_cost_usd", 0))
                        for effect in broker.ledger
                        if effect.branch_id == branch.branch_id and effect.novel
                    )
                    branch.model_spend_usd = round(measured, 9)
                    if (
                        branch.model_spend_usd
                        > branch.branch_spend_cap_usd - GRADING_SPEND_RESERVATION_USD
                    ):
                        branch.error = {
                            "type": "BranchModelSpendExceeded",
                            "message": "branch execution model spend exceeded its reserved cap",
                        }
                        raise BranchModelSpendExceeded(branch.error["message"])

                final_response = ""
                rebuilt_session = await session_service.get_session(
                    app_name=APP_NAME,
                    user_id="scenario",
                    session_id=session_id,
                )
                if rebuilt_session is None:
                    raise RuntimeError("rebuilt ADK session disappeared during continuation")
                new_session_events = rebuilt_session.events[reconstructed_event_count:]
                for adk_event in new_session_events:
                    usage, latency_ms, cost_usd = yielded_metadata.get(
                        str(adk_event.id), (TokenUsage(), 0.0, 0.0)
                    )
                    continuation_event = _branch_event(
                        run_id=run.run_id,
                        branch_id=branch.branch_id,
                        seq=len(branch_events),
                        adk_event=adk_event,
                        capabilities=branch.effective_capabilities,
                        phase="continuation",
                        source_event_seq=None,
                        token_usage=usage,
                        latency_ms=latency_ms,
                        cost_usd=cost_usd,
                    )
                    branch_events.append(continuation_event)
                    await self.store.write_branch_event(continuation_event)
                    if adk_event.is_final_response():
                        final_response = _text_from_event(adk_event)

                grades = await Adjudicator(driver=self.driver, store=self.store).grade_run(
                    run_id=run.run_id,
                    branch_id=branch.branch_id,
                    task=run.task,
                    criteria=run.criteria,
                    effects=broker.ledger,
                    final_response=final_response,
                    sandbox_name=sandbox_name,
                )

                raw_workspace = temp_dir / "branch-workspace.tar"
                self.driver.export_tar(sandbox_name, raw_workspace)
                workspace = zstandard.ZstdCompressor(level=6).compress(raw_workspace.read_bytes())
                artifact_prefix = f"runs/{run.run_id}/artifacts/{branch.branch_id}"
                branch.workspace_gcs_uri = await self.store.upload_bytes(
                    f"runs/{run.run_id}/branches/{branch.branch_id}/workspace.tar.zst",
                    workspace,
                    content_type="application/zstd",
                )
                branch.workspace_sha256 = hashlib.sha256(workspace).hexdigest()
                branch.workspace_bytes = len(workspace)
                branch.events_gcs_uri = await self.store.upload_bytes(
                    f"{artifact_prefix}/events.jsonl",
                    b"".join(
                        json_line_bytes(event.model_dump(mode="json"))
                        for event in branch_events
                    ),
                    content_type="application/x-ndjson",
                )
                branch.effects_gcs_uri = await self.store.upload_bytes(
                    f"{artifact_prefix}/effects.jsonl",
                    b"".join(
                        json_line_bytes(effect.model_dump(mode="json"))
                        for effect in broker.ledger
                    ),
                    content_type="application/x-ndjson",
                )
                branch.grades_gcs_uri = await self.store.upload_bytes(
                    f"{artifact_prefix}/grades.json",
                    json_bytes([grade.model_dump(mode="json") for grade in grades]),
                    content_type="application/json",
                )

                branch.final_response = final_response
                branch.verdict = "pass" if all(grade.passed for grade in grades) else "fail"
                branch.event_count = len(branch_events)
                branch.effect_count = len(broker.ledger)
                branch.inherited_effect_count = len(inherited)
                branch.novel_effect_count = sum(effect.novel for effect in broker.ledger)
                branch.completed_at = datetime.now(UTC)
                branch.duration_ms = round((time.perf_counter() - started) * 1000, 3)
                branch.accounted_spend_usd = round(
                    branch.model_spend_usd + GRADING_SPEND_RESERVATION_USD, 9
                )
                branch.status = "completed"
                branch.artifact_gcs_uri = f"gs://{self.bucket}/{artifact_prefix}/branch.json"
                result = {
                    "branch": branch.model_dump(mode="json"),
                    "events": [event.model_dump(mode="json") for event in branch_events],
                    "effects": [effect.model_dump(mode="json") for effect in broker.ledger],
                    "grades": [grade.model_dump(mode="json") for grade in grades],
                    "verification": {
                        "source_checkpoint_hash_matches": True,
                        "session_rebuilt_with_append_event": True,
                        "replayed_source_event_range": [0, branch.fork_seq],
                        "all_events_have_capabilities": all(
                            bool(event.capability_set) for event in branch_events
                        ),
                        "all_events_tagged_with_branch_id": all(
                            event.branch_id == branch.branch_id for event in branch_events
                        ),
                        "capabilities_do_not_exceed_original": capability_is_subset(
                            branch.effective_capabilities, branch.original_capabilities
                        ),
                        "broker_mode": "replay",
                        "novel_effect_count": branch.novel_effect_count,
                        "branch_wall_clock_limit_seconds": BRANCH_WALL_CLOCK_SECONDS,
                        "sandbox_command_timeout_seconds": self.driver.command_timeout,
                        "sandbox_output_limit_bytes": 256 * 1024,
                        "max_branches_per_investigation": MAX_BRANCHES_PER_INVESTIGATION,
                    },
                }
                await self.store.upload_bytes(
                    f"{artifact_prefix}/branch.json",
                    json_bytes(result),
                    content_type="application/json",
                )
                await self.store.write_branch(branch)
                return result
            finally:
                if runner is not None:
                    await runner.close()
                if sandbox_started:
                    self.driver.delete(sandbox_name)


def branch_service_from_env() -> BranchService:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    bucket = os.environ.get("CULPRIT_BUCKET", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    if not project or not bucket:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT and CULPRIT_BUCKET are required")
    return BranchService(project=project, bucket=bucket, location=location)
