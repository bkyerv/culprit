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
    error: dict[str, Any] | None = None


class Scenario(DomainModel):
    scenario_id: str
    task: str
    workspace_seed: str = "workspace"
    criteria: list[Criterion]
    capability_policy: CapabilitySet
    user_answers: dict[str, str] = Field(default_factory=dict)
