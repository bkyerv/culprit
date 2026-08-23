"""Pydantic domain model for recorded and replayable Culprit runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    egress_policy: Literal["deny", "brokered", "allow"] = "deny"
    effect_permissions: list[str] = Field(default_factory=list)

    @field_validator(
        "allowed_tools", "readable_paths", "writable_paths", "effect_permissions", mode="after"
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


class Scenario(DomainModel):
    scenario_id: str
    task: str
    workspace_seed: str = "workspace"
    criteria: list[Criterion]
    capability_policy: CapabilitySet
    user_answers: dict[str, str] = Field(default_factory=dict)
