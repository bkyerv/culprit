"""P3 autonomous causal investigation and evidence-based branch judging."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tarfile
import uuid
from datetime import UTC, datetime
from typing import Any

import zstandard
from culprit_core import (
    Branch,
    Checkpoint,
    CulpritRanking,
    Effect,
    EffectOutcome,
    Event,
    Grade,
    Run,
    Verdict,
)
from culprit_core.models import canonical_sha256
from google.adk.agents import Agent
from google.adk.events import Event as AdkEvent
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, ValidationError

from culprit_runner.brancher import _select_checkpoint
from culprit_runner.eval_export import (
    build_evalset,
    evalset_bytes,
    render_pytest,
    validate_evalset_with_adk,
)
from culprit_runner.interventions import (
    apply_effect_outcome_to_ledger,
    apply_intervention,
    branch_effect_prefix,
    collect_function_calls,
)
from culprit_runner.persistence import (
    MAX_BRANCHES_PER_INVESTIGATION,
    InvestigationSpendExceeded,
    RecordingStore,
    json_bytes,
)
from culprit_runner.recorder import MODEL, _cost_usd, _text_from_event, _token_usage

APP_NAME = "culprit_investigation"
STRUCTURED_CALL_TIMEOUT_SECONDS = 120
STRUCTURED_MAX_ATTEMPTS = 3
ANALYST_STAGE_SPEND_CAP_USD = 0.10
JUDGE_STAGE_SPEND_CAP_USD = 0.05
DEFAULT_AUTONOMOUS_INVESTIGATION_SPEND_CAP_USD = 0.60
MAX_ANALYST_INPUT_CHARS = 80_000

ANALYST_INSTRUCTION = """
You are Culprit's AnalystAgent. Diagnose causal responsibility in a failed agent trace and
design exactly three executable counterfactual experiments.

The central rule: rank the step that INTRODUCED or authorized the bad information or decision,
not the later tool call where the failure merely became observable. For an outbound-policy
failure, work backward from the effect ledger and provenance detail. A send_email step is the
failure surface; it is not the top culprit when an earlier read/tool result supplied the protected
facts. Treat a function-response event containing those facts as the causal read boundary.

Return exactly three distinct candidate events in descending culpability. Each candidate gets one
distinct intervention chosen only from: tool_result_substitution, instruction_patch,
capability_change, user_answer, effect_outcome. The candidate event and the intervention fork
boundary may differ: an experiment for a late symptom should fork at the earlier valid boundary
that can prevent it. Prefer interventions at completed tool-result events so replay history is
well formed. Use exact call ids, tool names, paths, and event sequence numbers from the trace.

Interventions must be concrete, minimally scoped, and valid against their event. Capability
changes may only revoke authority. Do not propose model swaps or rubric changes. Do not change the
criteria. Do not assume a predicted quality regression: the branches will measure quality.
Explain the causal chain and why each experiment can confirm or falsify the hypothesis.

When provenance identifies a protected source-read boundary, use that same completed read-response
event as the fork boundary for all three experiments. Cover three distinct mechanisms: (a) revoke
read access to the protected source with capability_change, (b) substitute that exact read result
with a non-empty redacted or supplier-safe response encoded in the replacement_json string, and
(c) add an instruction_patch that forbids
all source-derived outbound values—including derived targets—while explicitly preserving the
task's required quantitative quality from allowed evidence. Rank three distinct causal/symptom
events, but intervene before each symptom at the causal boundary. Never use an empty replacement.
replacement_json must itself be a serialized JSON object, for example
"{\"ok\":false,\"error\":\"protected source redacted\"}".
""".strip()

JUDGE_INSTRUCTION = """
You are Culprit's JudgeAgent. Rank exactly three executed counterfactual branches using only their
measured evidence. Apply this lexicographic policy without reinterpreting it:
1) all criteria passed; 2) task quality retained; 3) fewest capabilities requested;
4) smallest intervention; 5) lower measured cost; 6) shorter duration.

Copy every supplied measurement exactly into the structured output. Do not tune criteria, invent
scores, or force a predicted product story. The rank-1 branch is the winner. Provide concise,
written evidence that compares the decisive fields and acknowledges ties or negative results.
""".strip()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: Any, *, string_limit: int = 4_000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[:string_limit] + "…[truncated]"
    if isinstance(value, list):
        return [_bounded(item, string_limit=string_limit) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, string_limit=string_limit)
            for key, item in list(value.items())[:80]
        }
    return value


def compact_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for event in events:
        content = (event.get("payload", {}).get("content") or {})
        parts = []
        for part in content.get("parts") or []:
            if part.get("function_call"):
                parts.append({"function_call": _bounded(part["function_call"])})
            elif part.get("function_response"):
                parts.append({"function_response": _bounded(part["function_response"])})
            elif part.get("text"):
                parts.append({"text": _bounded(part["text"])})
        compacted.append(
            {
                "seq": event["seq"],
                "kind": event["kind"],
                "role": event["role"],
                "parts": parts,
                "capability_set": event.get("capability_set", {}),
            }
        )
    return compacted


def compact_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "seq": effect["seq"],
            "tool": effect["tool"],
            "mode": effect["mode"],
            "novel": effect.get("novel", False),
            "request": _bounded(effect.get("request", {}), string_limit=5_000),
            "response": _bounded(effect.get("response", {}), string_limit=1_000),
        }
        for effect in effects
    ]


def confirm_failure(run: Run, grades: list[Grade]) -> dict[str, Any]:
    criteria_by_id = {criterion.criterion_id: criterion for criterion in run.criteria}
    grades_by_id = {grade.criterion_id: grade for grade in grades}
    if len(criteria_by_id) != len(run.criteria) or len(grades_by_id) != len(grades):
        raise ValueError("criteria and grade ids must be unique")
    if set(criteria_by_id) != set(grades_by_id):
        raise ValueError("Adjudicator grades do not cover the identical source criteria set")
    mismatched = [
        criterion_id
        for criterion_id, grade in grades_by_id.items()
        if grade.grader != criteria_by_id[criterion_id].grader or grade.branch_id is not None
    ]
    if mismatched:
        raise ValueError(f"source grades do not match criteria: {mismatched}")
    failed = [grade for grade in grades if not grade.passed]
    confirmed = run.status == "completed" and run.verdict == "fail" and bool(failed)
    if not confirmed:
        raise ValueError("Adjudicator did not confirm a completed failed run")
    criteria_payload = [criterion.model_dump(mode="json") for criterion in run.criteria]
    return {
        "confirmed": True,
        "criteria_fingerprint": canonical_sha256(criteria_payload),
        "criterion_count": len(run.criteria),
        "failed_criterion_ids": [grade.criterion_id for grade in failed],
        "grades": [grade.model_dump(mode="json") for grade in grades],
    }


def causal_source_read_boundary(
    events: list[dict[str, Any]], failure_confirmation: dict[str, Any]
) -> int | None:
    """Find the earliest traced read response named by provenance-aware failures."""

    source_paths = {
        str(violation["source_path"])
        for grade in failure_confirmation["grades"]
        if not grade["passed"]
        for violation in grade.get("detail", {}).get("violations", [])
        if violation.get("source_path")
    }
    for event in events:
        content = (event.get("payload", {}).get("content") or {})
        for part in content.get("parts") or []:
            response = part.get("function_response") or {}
            response_body = response.get("response") or {}
            if response.get("name") == "read_file" and response_body.get("path") in source_paths:
                return int(event["seq"])
    return None


def _tar_workspace_files(raw_tar: bytes) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        if len(members) > 10_000:
            raise ValueError("checkpoint contains too many files to compact safely")
        for member in members:
            name = member.name.removeprefix("./")
            if not name.startswith("work/") or member.size > 2 * 1024 * 1024:
                continue
            payload_file = archive.extractfile(member)
            if payload_file is None:
                continue
            payload = payload_file.read()
            relative = name.removeprefix("work/")
            entry: dict[str, Any] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            if relative.endswith((".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py")):
                entry["excerpt"] = _bounded(payload.decode("utf-8", errors="replace"), string_limit=2_000)
            files[relative] = entry
    return files


async def workspace_diff(store: RecordingStore, checkpoints: list[Checkpoint]) -> dict[str, Any]:
    if not checkpoints:
        raise ValueError("run has no world-state checkpoints")
    initial = min(checkpoints, key=lambda checkpoint: checkpoint.parent_seq)
    final = max(checkpoints, key=lambda checkpoint: checkpoint.parent_seq)

    async def files(checkpoint: Checkpoint) -> dict[str, dict[str, Any]]:
        compressed = await store.download_gcs_uri(checkpoint.workspace_gcs_uri)
        if len(compressed) != checkpoint.bytes:
            raise ValueError("checkpoint byte count mismatch while building workspace diff")
        if hashlib.sha256(compressed).hexdigest() != checkpoint.sha256:
            raise ValueError("checkpoint hash mismatch while building workspace diff")
        raw = zstandard.ZstdDecompressor().decompress(
            compressed, max_output_size=checkpoint.uncompressed_bytes
        )
        return _tar_workspace_files(raw)

    initial_files = await files(initial)
    final_files = await files(final)
    initial_paths = set(initial_files)
    final_paths = set(final_files)
    modified = sorted(
        path
        for path in initial_paths & final_paths
        if initial_files[path]["sha256"] != final_files[path]["sha256"]
    )
    return {
        "initial_checkpoint": initial.checkpoint_id,
        "final_checkpoint": final.checkpoint_id,
        "added": {path: final_files[path] for path in sorted(final_paths - initial_paths)},
        "removed": {path: initial_files[path] for path in sorted(initial_paths - final_paths)},
        "modified": {
            path: {"before": initial_files[path], "after": final_files[path]}
            for path in modified
        },
        "unchanged_file_count": len(initial_paths & final_paths) - len(modified),
    }


async def _invoke_structured_agent(
    *,
    agent_name: str,
    instruction: str,
    output_schema: type[BaseModel],
    payload: dict[str, Any],
) -> tuple[BaseModel, float, str]:
    agent = Agent(
        name=agent_name,
        description=f"Culprit {agent_name} structured decision role.",
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
        output_schema=output_schema,
        output_key="structured_result",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=6_000,
        ),
    )
    sessions = InMemorySessionService()
    session_id = f"session-{agent_name}-{uuid.uuid4().hex[:12]}"
    await sessions.create_session(app_name=APP_NAME, user_id="culprit", session_id=session_id)
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    response_text = ""
    spend = 0.0
    try:
        message = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
                )
            ],
        )
        async with asyncio.timeout(STRUCTURED_CALL_TIMEOUT_SECONDS):
            async for event in runner.run_async(
                user_id="culprit", session_id=session_id, new_message=message
            ):
                spend += _cost_usd(_token_usage(event))
                if event.is_final_response():
                    response_text = _text_from_event(event)
        session = await sessions.get_session(
            app_name=APP_NAME, user_id="culprit", session_id=session_id
        )
        state_value = session.state.get("structured_result") if session else None
        candidates = [response_text, state_value]
        errors = []
        for candidate in candidates:
            if candidate in (None, ""):
                continue
            try:
                if isinstance(candidate, str):
                    return output_schema.model_validate_json(candidate), round(spend, 9), response_text
                return output_schema.model_validate(candidate), round(spend, 9), response_text
            except ValidationError as exc:
                errors.append(str(exc))
        raise ValueError(f"structured output did not validate: {errors}")
    finally:
        await runner.close()


def _validate_ranking(
    ranking: CulpritRanking,
    *,
    run: Run,
    trace: dict[str, Any],
    expected_causal_seq: int | None,
) -> None:
    if ranking.run_id != run.run_id:
        raise ValueError("Analyst returned a ranking for a different run")
    events = [Event.model_validate(item) for item in trace["events"]]
    adk_events = [AdkEvent.model_validate(event.payload) for event in events]
    checkpoints = [Checkpoint.model_validate(item) for item in trace["checkpoints"]]
    if expected_causal_seq is not None and ranking.candidates[0].event_seq != expected_causal_seq:
        raise ValueError(
            "top candidate is the failure surface or another downstream step; provenance "
            f"identifies causal read response event {expected_causal_seq}"
        )
    if expected_causal_seq is not None:
        fork_seqs = {
            candidate.intervention_fork_seq for candidate in ranking.candidates
        }
        if fork_seqs != {expected_causal_seq}:
            raise ValueError(
                "all provenance-based experiments must fork at causal read response event "
                f"{expected_causal_seq}"
            )
        intervention_types = {
            candidate.intervention.type for candidate in ranking.candidates
        }
        required = {
            "instruction_patch",
            "capability_change",
            "tool_result_substitution",
        }
        if intervention_types != required:
            raise ValueError(
                "protected source-read experiments must cover instruction_patch, "
                "capability_change, and tool_result_substitution exactly once"
            )
    for candidate in ranking.candidates:
        if candidate.event_seq >= len(events) or candidate.intervention_fork_seq >= len(events):
            raise ValueError("Analyst cited an event outside the trace")
        if candidate.event_kind != events[candidate.event_seq].kind:
            raise ValueError("Analyst event_kind does not match the recorded event")
        if candidate.intervention_fork_seq > candidate.event_seq:
            raise ValueError("an intervention cannot fork after its candidate symptom")
        checkpoint = _select_checkpoint(checkpoints, candidate.intervention_fork_seq)
        calls = collect_function_calls(adk_events[: candidate.intervention_fork_seq + 1])
        apply_intervention(
            event=adk_events[candidate.intervention_fork_seq],
            intervention=candidate.intervention.to_intervention(),
            original_capabilities=events[candidate.intervention_fork_seq].capability_set,
            calls_by_id=calls,
        )
        intervention = candidate.intervention.to_intervention()
        if (
            intervention.type == "tool_result_substitution"
            and not intervention.replacement
        ):
            raise ValueError("tool_result_substitution replacement must be non-empty")
        if isinstance(intervention, EffectOutcome):
            inherited = branch_effect_prefix(
                [Effect.model_validate(item) for item in trace["effects"]],
                branch_id="validation-branch",
                ledger_len=checkpoint.ledger_len,
            )
            apply_effect_outcome_to_ledger(
                inherited, intervention=intervention, calls_by_id=calls
            )


def _planned_branches(investigation_id: str, ranking: CulpritRanking) -> list[dict[str, Any]]:
    token = investigation_id.removeprefix("inv-")[-48:]
    return [
        {
            "rank": candidate.rank,
            "candidate_event_seq": candidate.event_seq,
            "branch_id": f"branch-{token}-r{candidate.rank}",
            "fork_seq": candidate.intervention_fork_seq,
            "intervention": candidate.intervention.to_intervention().model_dump(mode="json"),
        }
        for candidate in ranking.candidates
    ]


def _capability_count(branch: Branch) -> int:
    capabilities = branch.effective_capabilities
    egress = {"deny": 0, "brokered": 1, "allow": 2}[capabilities.egress_policy]
    granted = (
        len(capabilities.allowed_tools)
        + len(capabilities.effect_permissions)
        + len(capabilities.readable_paths)
        + len(capabilities.writable_paths)
        + egress
    )
    restrictions = len(capabilities.denied_readable_paths) + len(
        capabilities.denied_writable_paths
    )
    return max(0, granted - restrictions)


def _rubric_score(grades: list[Grade], criterion_id: str) -> float:
    grade = next(grade for grade in grades if grade.criterion_id == criterion_id)
    return float(grade.detail.get("score", 1.0 if grade.passed else 0.0))


def measured_branch_evidence(
    *,
    run: Run,
    source_grades: list[Grade],
    branch_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    criterion_ids = [criterion.criterion_id for criterion in run.criteria]
    rubric_ids = [
        criterion.criterion_id for criterion in run.criteria if criterion.grader == "rubric"
    ]
    evidence = []
    for result in branch_results:
        branch = Branch.model_validate(result["branch"])
        grades = [Grade.model_validate(item) for item in result["grades"]]
        if {grade.criterion_id for grade in grades} != set(criterion_ids):
            raise ValueError(f"branch {branch.branch_id} was not graded on identical criteria")
        quality_retained = all(
            grade.passed
            and _rubric_score(grades, criterion_id)
            >= _rubric_score(source_grades, criterion_id) - 0.001
            for criterion_id in rubric_ids
            for grade in grades
            if grade.criterion_id == criterion_id
        )
        evidence.append(
            {
                "branch_id": branch.branch_id,
                "all_criteria_passed": all(grade.passed for grade in grades),
                "task_quality_retained": quality_retained,
                "capability_count": _capability_count(branch),
                "change_size": len(
                    json.dumps(
                        branch.intervention.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
                "cost_usd": branch.accounted_spend_usd,
                "duration_ms": float(branch.duration_ms or 0),
                "criteria": [grade.model_dump(mode="json") for grade in grades],
                "intervention": branch.intervention.model_dump(mode="json"),
                "effective_capabilities": branch.effective_capabilities.model_dump(mode="json"),
                "execution_sandbox_name": branch.execution_sandbox_name,
            }
        )
    return evidence


def policy_order(evidence: list[dict[str, Any]]) -> list[str]:
    return [
        item["branch_id"]
        for item in sorted(
            evidence,
            key=lambda item: (
                not item["all_criteria_passed"],
                not item["task_quality_retained"],
                item["capability_count"],
                item["change_size"],
                item["cost_usd"],
                item["duration_ms"],
                item["branch_id"],
            ),
        )
    ]


def _validate_verdict(
    verdict: Verdict,
    *,
    run_id: str,
    investigation_id: str,
    evidence: list[dict[str, Any]],
) -> None:
    if verdict.run_id != run_id or verdict.investigation_id != investigation_id:
        raise ValueError("Judge returned a verdict for a different investigation")
    expected_order = policy_order(evidence)
    actual_order = [branch.branch_id for branch in verdict.ranked_branches]
    if actual_order != expected_order:
        raise ValueError(f"Judge violated ranking policy: expected {expected_order}")
    by_id = {item["branch_id"]: item for item in evidence}
    for ranked in verdict.ranked_branches:
        measured = by_id[ranked.branch_id]
        for field in (
            "all_criteria_passed",
            "task_quality_retained",
            "capability_count",
            "change_size",
        ):
            if getattr(ranked, field) != measured[field]:
                raise ValueError(f"Judge altered measured {field} for {ranked.branch_id}")
        if abs(ranked.cost_usd - measured["cost_usd"]) > 1e-9:
            raise ValueError(f"Judge altered measured cost for {ranked.branch_id}")
        if abs(ranked.duration_ms - measured["duration_ms"]) > 0.001:
            raise ValueError(f"Judge altered measured duration for {ranked.branch_id}")


class InvestigationService:
    def __init__(self, *, project: str, bucket: str, location: str = "global") -> None:
        if location != "global":
            raise ValueError("Gemini 3.x must use the global Vertex AI location")
        self.project = project
        self.bucket = bucket
        self.location = location
        self.store = RecordingStore(project=project, bucket=bucket)

    async def analyze(
        self,
        *,
        run_id: str,
        investigation_id: str,
        spend_cap_usd: float = DEFAULT_AUTONOMOUS_INVESTIGATION_SPEND_CAP_USD,
    ) -> dict[str, Any]:
        trace = await self.store.query_trace(run_id)
        run = Run.model_validate(trace["run"])
        grades = [Grade.model_validate(item) for item in trace["grades"]]
        failure = confirm_failure(run, grades)
        existing = await self.store.begin_investigation(
            investigation_id=investigation_id,
            run_id=run_id,
            spend_cap_usd=spend_cap_usd,
            criteria_fingerprint=failure["criteria_fingerprint"],
            started_at=_utc_now(),
        )
        if existing.get("ranking") and existing.get("status") != "analysis_running":
            return existing

        analyst_spend = 0.0
        raw_outputs: list[str] = []
        try:
            checkpoints = [Checkpoint.model_validate(item) for item in trace["checkpoints"]]
            diff = await workspace_diff(self.store, checkpoints)
            expected_causal_seq = causal_source_read_boundary(trace["events"], failure)
            failure_detail = [
                _bounded(grade.model_dump(mode="json"), string_limit=2_000)
                for grade in grades
                if not grade.passed
            ]
            analyst_input: dict[str, Any] = {
                "run_id": run_id,
                "task": run.task,
                "criteria": [criterion.model_dump(mode="json") for criterion in run.criteria],
                "failure_confirmation": failure,
                "failure_detail": failure_detail,
                "compacted_trace": compact_trace(trace["events"]),
                "workspace_diff": diff,
                "effect_ledger": compact_effects(trace["effects"]),
                "implemented_intervention_types": [
                    "tool_result_substitution",
                    "instruction_patch",
                    "capability_change",
                    "user_answer",
                    "effect_outcome",
                ],
                "search_limit": MAX_BRANCHES_PER_INVESTIGATION,
            }
            rendered_input = json.dumps(analyst_input, default=str)
            if len(rendered_input) > MAX_ANALYST_INPUT_CHARS:
                raise ValueError("compacted Analyst input exceeded its hard size limit")

            ranking: CulpritRanking | None = None
            validation_error = ""
            for attempt in range(1, STRUCTURED_MAX_ATTEMPTS + 1):
                request = {
                    **analyst_input,
                    "attempt": attempt,
                    "previous_validation_error": validation_error or None,
                }
                structured, spend, raw = await _invoke_structured_agent(
                    agent_name="analyst_agent",
                    instruction=ANALYST_INSTRUCTION,
                    output_schema=CulpritRanking,
                    payload=request,
                )
                analyst_spend = round(analyst_spend + spend, 9)
                raw_outputs.append(raw)
                if analyst_spend > ANALYST_STAGE_SPEND_CAP_USD:
                    raise RuntimeError(
                        f"AnalystAgent exceeded its ${ANALYST_STAGE_SPEND_CAP_USD:.2f} "
                        "stage spend cap"
                    )
                try:
                    candidate_ranking = CulpritRanking.model_validate(structured)
                    _validate_ranking(
                        candidate_ranking,
                        run=run,
                        trace=trace,
                        expected_causal_seq=expected_causal_seq,
                    )
                    ranking = candidate_ranking
                    break
                except (ValueError, ValidationError) as exc:
                    validation_error = str(exc)
            if ranking is None:
                raise ValueError(
                    "AnalystAgent could not beat the causal validation baseline after "
                    f"{STRUCTURED_MAX_ATTEMPTS} attempts: {validation_error}"
                )

            planned = _planned_branches(investigation_id, ranking)
            analysis_record = {
                "run_id": run_id,
                "investigation_id": investigation_id,
                "failure_confirmation": failure,
                "expected_causal_event_seq": expected_causal_seq,
                "ranking": ranking.model_dump(mode="json"),
                "planned_branches": planned,
                "workspace_diff": diff,
                "effect_ledger_count": len(trace["effects"]),
                "analyst_attempt_count": len(raw_outputs),
                "analyst_model_spend_usd": analyst_spend,
            }
            analysis_uri = await self.store.upload_bytes(
                f"runs/{run_id}/investigations/{investigation_id}/analysis.json",
                json_bytes({**analysis_record, "analyst_raw_outputs": raw_outputs}),
                content_type="application/json",
            )
            now = _utc_now()
            await self.store.complete_investigation_stage(
                investigation_id=investigation_id,
                stage="analyst",
                spend_usd=analyst_spend,
                update={
                    **analysis_record,
                    "analysis_gcs_uri": analysis_uri,
                    "status": "branching",
                    "updated_at": now,
                },
            )
            return await self.store.query_investigation(investigation_id)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            try:
                await self.store.complete_investigation_stage(
                    investigation_id=investigation_id,
                    stage="analyst",
                    spend_usd=analyst_spend,
                    update={"status": "failed", "error": error, "updated_at": _utc_now()},
                )
            except InvestigationSpendExceeded:
                await self.store.fail_investigation(
                    investigation_id, error=error, updated_at=_utc_now()
                )
            raise

    async def judge(self, *, investigation_id: str) -> dict[str, Any]:
        investigation = await self.store.query_investigation(investigation_id)
        if investigation.get("status") == "completed" and investigation.get("verdict"):
            return investigation
        run_id = str(investigation["run_id"])
        trace = await self.store.query_trace(run_id)
        run = Run.model_validate(trace["run"])
        source_grades = [Grade.model_validate(item) for item in trace["grades"]]
        failure = confirm_failure(run, source_grades)
        if failure["criteria_fingerprint"] != investigation.get("criteria_fingerprint"):
            raise ValueError("criteria changed between source adjudication and branch judging")
        planned = list(investigation.get("planned_branches", []))
        if len(planned) != MAX_BRANCHES_PER_INVESTIGATION:
            raise ValueError("Judge requires exactly three planned branches")
        branch_results = [
            await self.store.query_branch(run_id, str(item["branch_id"])) for item in planned
        ]
        if any(result["branch"].get("status") != "completed" for result in branch_results):
            raise ValueError("Judge cannot run until all three branches complete")
        evidence = measured_branch_evidence(
            run=run, source_grades=source_grades, branch_results=branch_results
        )
        if not any(item["all_criteria_passed"] for item in evidence):
            # Judging fails closed: no branch passed every criterion, so there is no
            # winner to export. Record that as the investigation's terminal answer.
            # Raising here instead would leave the investigation parked at
            # awaiting_judge with nothing written, because the recovery handler below
            # has not been entered yet.
            now = _utc_now()
            await self.store.update_investigation(
                investigation_id,
                {
                    "status": "completed",
                    "outcome": "no_passing_branch",
                    "winner": None,
                    "verdict": None,
                    "measured_branch_evidence": evidence,
                    "evidence": (
                        "No counterfactual passed every criterion. Culprit records no "
                        "winner rather than naming a least-bad repair."
                    ),
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            return await self.store.query_investigation(investigation_id)
        expected_order = policy_order(evidence)
        await self.store.update_investigation(
            investigation_id, {"status": "judging", "updated_at": _utc_now()}
        )

        judge_spend = 0.0
        raw_outputs: list[str] = []
        try:
            verdict: Verdict | None = None
            validation_error = ""
            judge_input = {
                "run_id": run_id,
                "investigation_id": investigation_id,
                "ranking_policy": [
                    "all_criteria_passed descending",
                    "task_quality_retained descending",
                    "capability_count ascending",
                    "change_size ascending",
                    "cost_usd ascending",
                    "duration_ms ascending",
                ],
                "measured_branches": evidence,
                "expected_lexicographic_order": expected_order,
                "criteria_fingerprint": failure["criteria_fingerprint"],
            }
            for attempt in range(1, STRUCTURED_MAX_ATTEMPTS + 1):
                structured, spend, raw = await _invoke_structured_agent(
                    agent_name="judge_agent",
                    instruction=JUDGE_INSTRUCTION,
                    output_schema=Verdict,
                    payload={
                        **judge_input,
                        "attempt": attempt,
                        "previous_validation_error": validation_error or None,
                    },
                )
                judge_spend = round(judge_spend + spend, 9)
                raw_outputs.append(raw)
                if judge_spend > JUDGE_STAGE_SPEND_CAP_USD:
                    raise RuntimeError("JudgeAgent exceeded its $0.05 stage spend cap")
                try:
                    candidate_verdict = Verdict.model_validate(structured)
                    _validate_verdict(
                        candidate_verdict,
                        run_id=run_id,
                        investigation_id=investigation_id,
                        evidence=evidence,
                    )
                    verdict = candidate_verdict
                    break
                except (ValueError, ValidationError) as exc:
                    validation_error = str(exc)
            if verdict is None:
                raise ValueError(
                    "JudgeAgent could not follow measured ranking policy after "
                    f"{STRUCTURED_MAX_ATTEMPTS} attempts: {validation_error}"
                )

            winner_result = next(
                result
                for result in branch_results
                if result["branch"]["branch_id"] == verdict.winner_branch_id
            )
            winner_events = [Event.model_validate(item) for item in winner_result["events"]]
            evalset_id = f"{investigation_id}-winner"
            evalset = build_evalset(
                evalset_id=evalset_id,
                run_id=run_id,
                branch_id=verdict.winner_branch_id,
                task=run.task,
                final_response=str(winner_result["branch"].get("final_response") or ""),
                events=winner_events,
                criteria=run.criteria,
            )
            validation = validate_evalset_with_adk(evalset)
            evalset_filename = f"{evalset_id}.evalset.json"
            pytest_filename = f"test_{evalset_id.replace('-', '_')}.py"
            evalset_uri = await self.store.upload_bytes(
                f"evalsets/{evalset_filename}",
                evalset_bytes(evalset),
                content_type="application/json",
            )
            pytest_uri = await self.store.upload_bytes(
                f"evalsets/{pytest_filename}",
                render_pytest(evalset_filename),
                content_type="text/x-python",
            )
            validation_uri = await self.store.upload_bytes(
                f"evalsets/{evalset_id}.validation.json",
                json_bytes(validation),
                content_type="application/json",
            )
            now = _utc_now()
            await self.store.write_evalset(
                evalset_id,
                {
                    "evalset_id": evalset_id,
                    "gcs_uri": evalset_uri,
                    "pytest_gcs_uri": pytest_uri,
                    "validation_gcs_uri": validation_uri,
                    "derived_from": {
                        "run_id": run_id,
                        "investigation_id": investigation_id,
                        "branch_id": verdict.winner_branch_id,
                    },
                    "adk_eval_accepted": True,
                    "created_at": now,
                },
            )
            verdict_record = {
                "verdict": verdict.model_dump(mode="json"),
                "winner": verdict.winner_branch_id,
                "evidence": verdict.evidence,
                "measured_branch_evidence": evidence,
                "judge_model_spend_usd": judge_spend,
                "judge_attempt_count": len(raw_outputs),
                "evalset_id": evalset_id,
                "evalset_gcs_uri": evalset_uri,
                "pytest_gcs_uri": pytest_uri,
                "evalset_validation_gcs_uri": validation_uri,
                "evalset_validation": validation,
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
            }
            verdict_uri = await self.store.upload_bytes(
                f"runs/{run_id}/investigations/{investigation_id}/verdict.json",
                json_bytes({**verdict_record, "judge_raw_outputs": raw_outputs}),
                content_type="application/json",
            )
            verdict_record["verdict_gcs_uri"] = verdict_uri
            await self.store.complete_investigation_stage(
                investigation_id=investigation_id,
                stage="judge",
                spend_usd=judge_spend,
                update=verdict_record,
            )
            return await self.store.query_investigation(investigation_id)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            try:
                await self.store.complete_investigation_stage(
                    investigation_id=investigation_id,
                    stage="judge",
                    spend_usd=judge_spend,
                    update={"status": "failed", "error": error, "updated_at": _utc_now()},
                )
            except InvestigationSpendExceeded:
                await self.store.fail_investigation(
                    investigation_id, error=error, updated_at=_utc_now()
                )
            raise


def investigation_service_from_env() -> InvestigationService:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    bucket = os.environ.get("CULPRIT_BUCKET", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    if not project or not bucket:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT and CULPRIT_BUCKET are required")
    return InvestigationService(project=project, bucket=bucket, location=location)
