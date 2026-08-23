"""Pydantic domain model for recorded and replayable Culprit runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class EffectMode(StrEnum):
    SIMULATE = "simulate"
    RECORD = "record"
    REPLAY = "replay"


class CapabilitySet(DomainModel):
    """The exact authority available to an agent at one event."""

    allowed_tools: list[str] = Field(default_factory=list)
    readable_paths: list[str] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=list)
    denied_readable_paths: list[str] = Field(default_factory=list)
    denied_writable_paths: list[str] = Field(default_factory=list)
    egress_policy: Literal["deny", "brokered", "allow"] = "deny"
    effect_permissions: list[str] = Field(default_factory=list)

    @field_validator(
        "allowed_tools",
        "readable_paths",
        "writable_paths",
        "denied_readable_paths",
        "denied_writable_paths",
        "effect_permissions",
        mode="after",
    )
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("capability entries must be unique")
        return values


class Criterion(DomainModel):
    criterion_id: str
    grader: Literal["command", "invariant", "rubric", "schema"]
    description: str
    config: dict[str, Any] = Field(default_factory=dict)


class Grade(DomainModel):
    run_id: str
    criterion_id: str
    grader: Literal["command", "invariant", "rubric", "schema"]
    passed: bool
    detail: dict[str, Any] = Field(default_factory=dict)
    branch_id: str | None = None
    graded_at: datetime = Field(default_factory=utc_now)


class TokenUsage(DomainModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class Effect(DomainModel):
    run_id: str
    seq: int = Field(ge=0)
    tool: str
    args_hash: str
    mode: EffectMode
    novel: bool = False
    request: dict[str, Any]
    response: dict[str, Any]
    branch_id: str | None = None
    inherited: bool = False
    source_effect_seq: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    latency_ms: float = Field(ge=0)

    @classmethod
    def hash_request(cls, tool: str, request: dict[str, Any]) -> str:
        return canonical_sha256({"tool": tool, "request": request})


class Event(DomainModel):
    run_id: str
    seq: int = Field(ge=0)
    event_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    role: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    capability_set: CapabilitySet
    branch_id: str | None = None
    phase: Literal["recording", "replay", "continuation"] = "recording"
    source_event_seq: int | None = Field(default=None, ge=0)


class Checkpoint(DomainModel):
    """Both halves of world state at one event boundary."""

    run_id: str
    checkpoint_id: str
    workspace_gcs_uri: str
    sha256: str
    bytes: int = Field(ge=0)
    uncompressed_bytes: int = Field(ge=0)
    parent_seq: int = Field(ge=-1)
    ledger_len: int = Field(ge=0)
    effect_ledger_slice: list[Effect] = Field(default_factory=list)
    effect_ledger_sha256: str
    created_at: datetime = Field(default_factory=utc_now)


class Run(DomainModel):
    run_id: str
    scenario_id: str
    task: str
    status: Literal["starting", "running", "completed", "failed"] = "starting"
    model: str
    criteria: list[Criterion]
    capabilities: CapabilitySet
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    final_response: str | None = None
    error: dict[str, Any] | None = None
    event_count: int = Field(default=0, ge=0)
    effect_count: int = Field(default=0, ge=0)
    checkpoint_count: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    trace_gcs_uri: str | None = None
    verdict: Literal["pass", "fail"] | None = None


class ToolResultSubstitution(DomainModel):
    type: Literal["tool_result_substitution"]
    call_id: str
    tool_name: str
    replacement: dict[str, Any]


class InstructionPatch(DomainModel):
    type: Literal["instruction_patch"]
    instruction: str = Field(min_length=1)


class CapabilityChange(DomainModel):
    type: Literal["capability_change"]
    remove_allowed_tools: list[str] = Field(default_factory=list)
    revoke_readable_paths: list[str] = Field(default_factory=list)
    revoke_writable_paths: list[str] = Field(default_factory=list)
    remove_effect_permissions: list[str] = Field(default_factory=list)
    egress_policy: Literal["deny", "brokered", "allow"] | None = None

    @model_validator(mode="after")
    def requires_a_change(self) -> CapabilityChange:
        if not any(
            (
                self.remove_allowed_tools,
                self.revoke_readable_paths,
                self.revoke_writable_paths,
                self.remove_effect_permissions,
                self.egress_policy is not None,
            )
        ):
            raise ValueError("capability_change must revoke at least one capability")
        return self


class UserAnswer(DomainModel):
    type: Literal["user_answer"]
    call_id: str
    answer: str


class EffectOutcome(DomainModel):
    type: Literal["effect_outcome"]
    call_id: str
    tool_name: str
    replacement: dict[str, Any]


Intervention = Annotated[
    ToolResultSubstitution | InstructionPatch | CapabilityChange | UserAnswer | EffectOutcome,
    Field(discriminator="type"),
]


class InterventionProposal(DomainModel):
    """Gemini-compatible flat schema that validates into one implemented intervention."""

    type: Literal[
        "tool_result_substitution",
        "instruction_patch",
        "capability_change",
        "user_answer",
        "effect_outcome",
    ]
    call_id: str | None = None
    tool_name: str | None = None
    replacement_json: str | None = None
    instruction: str | None = None
    answer: str | None = None
    remove_allowed_tools: list[str] = Field(default_factory=list)
    revoke_readable_paths: list[str] = Field(default_factory=list)
    revoke_writable_paths: list[str] = Field(default_factory=list)
    remove_effect_permissions: list[str] = Field(default_factory=list)
    egress_policy: Literal["deny", "brokered", "allow"] | None = None

    @model_validator(mode="after")
    def matches_implemented_type(self) -> InterventionProposal:
        self.to_intervention()
        return self

    def to_intervention(self) -> Intervention:
        replacement: dict[str, Any] | None = None
        if self.replacement_json is not None:
            try:
                decoded = json.loads(self.replacement_json)
            except json.JSONDecodeError as exc:
                raise ValueError("replacement_json must contain valid JSON") from exc
            if not isinstance(decoded, dict):
                raise ValueError("replacement_json must encode a JSON object")
            replacement = decoded
        shared = {
            "type": self.type,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "replacement": replacement,
            "instruction": self.instruction,
            "answer": self.answer,
            "remove_allowed_tools": self.remove_allowed_tools,
            "revoke_readable_paths": self.revoke_readable_paths,
            "revoke_writable_paths": self.revoke_writable_paths,
            "remove_effect_permissions": self.remove_effect_permissions,
            "egress_policy": self.egress_policy,
        }
        allowed_fields = {
            "tool_result_substitution": {"type", "call_id", "tool_name", "replacement"},
            "instruction_patch": {"type", "instruction"},
            "capability_change": {
                "type",
                "remove_allowed_tools",
                "revoke_readable_paths",
                "revoke_writable_paths",
                "remove_effect_permissions",
                "egress_policy",
            },
            "user_answer": {"type", "call_id", "answer"},
            "effect_outcome": {"type", "call_id", "tool_name", "replacement"},
        }[self.type]
        populated_extras = {
            key: value
            for key, value in shared.items()
            if key not in allowed_fields and value not in (None, [], {})
        }
        if populated_extras:
            raise ValueError(
                f"{self.type} contains fields for another intervention type: "
                f"{sorted(populated_extras)}"
            )
        payload = {key: value for key, value in shared.items() if key in allowed_fields}
        model = {
            "tool_result_substitution": ToolResultSubstitution,
            "instruction_patch": InstructionPatch,
            "capability_change": CapabilityChange,
            "user_answer": UserAnswer,
            "effect_outcome": EffectOutcome,
        }[self.type]
        return model.model_validate(payload)


class Branch(DomainModel):
    run_id: str
    branch_id: str
    investigation_id: str
    fork_seq: int = Field(ge=0)
    intervention: Intervention
    status: Literal["starting", "running", "completed", "failed", "aborted"] = "starting"
    source_checkpoint_id: str | None = None
    source_checkpoint_parent_seq: int | None = Field(default=None, ge=-1)
    source_checkpoint_gcs_uri: str | None = None
    source_ledger_len: int = Field(default=0, ge=0)
    original_capabilities: CapabilitySet
    effective_capabilities: CapabilitySet
    capability_derivations: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    final_response: str | None = None
    verdict: Literal["pass", "fail"] | None = None
    event_count: int = Field(default=0, ge=0)
    effect_count: int = Field(default=0, ge=0)
    inherited_effect_count: int = Field(default=0, ge=0)
    novel_effect_count: int = Field(default=0, ge=0)
    model_spend_usd: float = Field(default=0, ge=0)
    grading_spend_reservation_usd: float = Field(default=0, ge=0)
    accounted_spend_usd: float = Field(default=0, ge=0)
    branch_spend_cap_usd: float = Field(gt=0)
    investigation_spend_cap_usd: float = Field(gt=0)
    workspace_gcs_uri: str | None = None
    workspace_sha256: str | None = None
    workspace_bytes: int | None = Field(default=None, ge=0)
    events_gcs_uri: str | None = None
    effects_gcs_uri: str | None = None
    grades_gcs_uri: str | None = None
    artifact_gcs_uri: str | None = None
    applied_intervention: dict[str, Any] = Field(default_factory=dict)
    execution_sandbox_name: str | None = None
    error: dict[str, Any] | None = None


class CulpritCandidate(DomainModel):
    """One causal hypothesis and the executable experiment that tests it."""

    rank: int = Field(ge=1, le=3)
    event_seq: int = Field(ge=0)
    event_kind: str
    summary: str = Field(min_length=1)
    culpability_score: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    intervention_fork_seq: int = Field(ge=0)
    intervention: InterventionProposal


class CulpritRanking(DomainModel):
    """Structured AnalystAgent output for the bounded K=3 search."""

    run_id: str
    failure_summary: str = Field(min_length=1)
    candidates: list[CulpritCandidate] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def candidates_are_ranked(self) -> CulpritRanking:
        if [candidate.rank for candidate in self.candidates] != [1, 2, 3]:
            raise ValueError("candidate ranks must be exactly [1, 2, 3]")
        scores = [candidate.culpability_score for candidate in self.candidates]
        if scores != sorted(scores, reverse=True):
            raise ValueError("candidate culpability scores must be descending")
        event_seqs = [candidate.event_seq for candidate in self.candidates]
        if len(event_seqs) != len(set(event_seqs)):
            raise ValueError("candidate event sequences must be unique")
        branch_specs = [
            canonical_sha256(
                {
                    "fork_seq": candidate.intervention_fork_seq,
                    "intervention": candidate.intervention.to_intervention().model_dump(
                        mode="json"
                    ),
                }
            )
            for candidate in self.candidates
        ]
        if len(branch_specs) != len(set(branch_specs)):
            raise ValueError("candidate branch experiments must be unique")
        return self


class RankedBranch(DomainModel):
    rank: int = Field(ge=1, le=3)
    branch_id: str
    all_criteria_passed: bool
    task_quality_retained: bool
    capability_count: int = Field(ge=0)
    change_size: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    duration_ms: float = Field(ge=0)
    rationale: str = Field(min_length=1)


class Verdict(DomainModel):
    """Structured JudgeAgent result after all counterfactuals are measured."""

    run_id: str
    investigation_id: str
    winner_branch_id: str
    ranked_branches: list[RankedBranch] = Field(min_length=3, max_length=3)
    evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def branches_are_ranked(self) -> Verdict:
        if [branch.rank for branch in self.ranked_branches] != [1, 2, 3]:
            raise ValueError("branch ranks must be exactly [1, 2, 3]")
        branch_ids = [branch.branch_id for branch in self.ranked_branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("ranked branch ids must be unique")
        if self.winner_branch_id != branch_ids[0]:
            raise ValueError("winner_branch_id must be the rank-1 branch")
        return self


class Scenario(DomainModel):
    scenario_id: str
    task: str
    workspace_seed: str = "workspace"
    criteria: list[Criterion]
    capability_policy: CapabilitySet
    user_answers: dict[str, str] = Field(default_factory=dict)
