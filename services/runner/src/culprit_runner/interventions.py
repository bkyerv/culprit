"""Validated P2 intervention application and capability non-escalation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from culprit_core.models import (
    CapabilityChange,
    CapabilitySet,
    Effect,
    EffectMode,
    EffectOutcome,
    InstructionPatch,
    Intervention,
    ToolResultSubstitution,
    UserAnswer,
)
from google.adk.events import Event as AdkEvent

from culprit_runner.tools import _normalise_path, path_is_allowed

EGRESS_AUTHORITY = {"deny": 0, "brokered": 1, "allow": 2}
EFFECT_TOOLS = {"send_email", "http_request", "schedule_event", "post_message"}


@dataclass(frozen=True)
class AppliedIntervention:
    event: AdkEvent
    capabilities: CapabilitySet
    instruction_patch: str | None
    detail: dict[str, Any]


def capability_is_subset(candidate: CapabilitySet, original: CapabilitySet) -> bool:
    """Return true only when candidate authority cannot exceed original authority."""

    return all(
        (
            set(candidate.allowed_tools) <= set(original.allowed_tools),
            set(candidate.effect_permissions) <= set(original.effect_permissions),
            candidate.readable_paths == original.readable_paths,
            candidate.writable_paths == original.writable_paths,
            set(candidate.denied_readable_paths) >= set(original.denied_readable_paths),
            set(candidate.denied_writable_paths) >= set(original.denied_writable_paths),
            EGRESS_AUTHORITY[candidate.egress_policy] <= EGRESS_AUTHORITY[original.egress_policy],
        )
    )


def apply_capability_change(
    original: CapabilitySet, intervention: CapabilityChange
) -> tuple[CapabilitySet, list[str]]:
    """Apply only authority-reducing changes and record derived restrictions."""

    missing_tools = set(intervention.remove_allowed_tools) - set(original.allowed_tools)
    missing_effects = set(intervention.remove_effect_permissions) - set(original.effect_permissions)
    if missing_tools:
        raise ValueError(f"cannot revoke tools not present: {sorted(missing_tools)}")
    if missing_effects:
        raise ValueError(f"cannot revoke effect permissions not present: {sorted(missing_effects)}")
    if (
        intervention.egress_policy is not None
        and EGRESS_AUTHORITY[intervention.egress_policy] > EGRESS_AUTHORITY[original.egress_policy]
    ):
        raise ValueError("capability_change cannot increase egress authority")

    removed_tools = set(intervention.remove_allowed_tools)
    derivations: list[str] = []
    if (
        intervention.revoke_readable_paths or intervention.revoke_writable_paths
    ) and "run_command" in original.allowed_tools:
        # An unrestricted shell would bypass path-level tool checks. Removing it
        # is the fail-closed consequence of enforcing a filesystem revocation.
        removed_tools.add("run_command")
        derivations.append(
            "run_command revoked automatically because unrestricted shell access "
            "would bypass path-level revocations"
        )

    candidate = original.model_copy(
        update={
            "allowed_tools": [tool for tool in original.allowed_tools if tool not in removed_tools],
            "effect_permissions": [
                permission
                for permission in original.effect_permissions
                if permission not in set(intervention.remove_effect_permissions)
            ],
            "denied_readable_paths": list(
                dict.fromkeys(
                    [
                        *original.denied_readable_paths,
                        *intervention.revoke_readable_paths,
                    ]
                )
            ),
            "denied_writable_paths": list(
                dict.fromkeys(
                    [
                        *original.denied_writable_paths,
                        *intervention.revoke_writable_paths,
                    ]
                )
            ),
            "egress_policy": intervention.egress_policy or original.egress_policy,
        },
        deep=True,
    )
    if not capability_is_subset(candidate, original):
        raise ValueError("capability_change would silently increase authority")
    return candidate, derivations


def collect_function_calls(events: list[AdkEvent]) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for event in events:
        for call in event.get_function_calls():
            if not call.id:
                raise ValueError("recorded function call is missing an id")
            calls[call.id] = {"name": call.name, "args": deepcopy(call.args or {})}
    return calls


def _matching_response(event: AdkEvent, *, call_id: str, tool_name: str):
    matches = [
        response
        for response in event.get_function_responses()
        if response.id == call_id and response.name == tool_name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"intervention expected one {tool_name} response for {call_id}, found {len(matches)}"
        )
    return matches[0]


def _call_denial(call: dict[str, Any], capabilities: CapabilitySet) -> str | None:
    name = str(call["name"])
    args = dict(call.get("args", {}))
    if name not in capabilities.allowed_tools:
        return f"tool is not allowed: {name}"
    if name in EFFECT_TOOLS and name not in capabilities.effect_permissions:
        return f"effect permission is not allowed: {name}"
    if name in {"read_file", "list_dir"}:
        path = _normalise_path(str(args.get("path", ".")))
        if not path_is_allowed(
            path,
            allowed=capabilities.readable_paths,
            denied=capabilities.denied_readable_paths,
        ):
            return f"read is not allowed: {args.get('path', '.')}"
    if name == "write_file":
        path = _normalise_path(str(args.get("path", "")))
        if not path_is_allowed(
            path,
            allowed=capabilities.writable_paths,
            denied=capabilities.denied_writable_paths,
        ):
            return f"write is not allowed: {args.get('path', '')}"
    return None


def _enforce_capabilities_on_result(
    event: AdkEvent,
    *,
    calls_by_id: dict[str, dict[str, Any]],
    capabilities: CapabilitySet,
) -> list[dict[str, str]]:
    rewritten: list[dict[str, str]] = []
    for response in event.get_function_responses():
        call = calls_by_id.get(str(response.id))
        if call is None:
            raise ValueError(f"function response has no recorded call: {response.id}")
        denial = _call_denial(call, capabilities)
        if denial is None:
            continue
        response.response = {
            "ok": False,
            "error": denial,
            "intervention": "capability_change",
        }
        rewritten.append(
            {"call_id": str(response.id), "tool_name": str(response.name), "error": denial}
        )
    return rewritten


def apply_intervention(
    *,
    event: AdkEvent,
    intervention: Intervention,
    original_capabilities: CapabilitySet,
    calls_by_id: dict[str, dict[str, Any]],
) -> AppliedIntervention:
    """Apply one of the five P2 interventions exactly at the fork event."""

    rewritten = event.model_copy(deep=True)
    capabilities = original_capabilities.model_copy(deep=True)
    instruction_patch: str | None = None
    detail: dict[str, Any] = {"type": intervention.type, "event_id": rewritten.id}

    if isinstance(intervention, ToolResultSubstitution):
        response = _matching_response(
            rewritten,
            call_id=intervention.call_id,
            tool_name=intervention.tool_name,
        )
        response.response = deepcopy(intervention.replacement)
        detail["rewritten_call_ids"] = [intervention.call_id]
    elif isinstance(intervention, InstructionPatch):
        instruction_patch = intervention.instruction
        detail["instruction_appended"] = intervention.instruction
    elif isinstance(intervention, CapabilityChange):
        capabilities, derivations = apply_capability_change(original_capabilities, intervention)
        detail["capability_derivations"] = derivations
        detail["rewritten_responses"] = _enforce_capabilities_on_result(
            rewritten,
            calls_by_id=calls_by_id,
            capabilities=capabilities,
        )
    elif isinstance(intervention, UserAnswer):
        response = _matching_response(rewritten, call_id=intervention.call_id, tool_name="ask_user")
        existing = dict(response.response or {})
        response.response = {**existing, "answer": intervention.answer, "source": "intervention"}
        detail["rewritten_call_ids"] = [intervention.call_id]
    elif isinstance(intervention, EffectOutcome):
        response = _matching_response(
            rewritten,
            call_id=intervention.call_id,
            tool_name=intervention.tool_name,
        )
        response.response = deepcopy(intervention.replacement)
        detail["rewritten_call_ids"] = [intervention.call_id]
    else:  # The discriminated Pydantic union keeps this fail-closed.
        raise TypeError(f"unsupported intervention: {type(intervention).__name__}")

    detail["effective_capabilities"] = capabilities.model_dump(mode="json")
    return AppliedIntervention(
        event=rewritten,
        capabilities=capabilities,
        instruction_patch=instruction_patch,
        detail=detail,
    )


def branch_effect_prefix(effects: list[Effect], *, branch_id: str, ledger_len: int) -> list[Effect]:
    if ledger_len > len(effects):
        raise ValueError("checkpoint ledger length exceeds recorded effects")
    return [
        effect.model_copy(
            update={
                "mode": EffectMode.REPLAY,
                "novel": False,
                "branch_id": branch_id,
                "inherited": True,
                "source_effect_seq": effect.seq,
            },
            deep=True,
        )
        for effect in effects[:ledger_len]
    ]


def apply_effect_outcome_to_ledger(
    ledger: list[Effect],
    *,
    intervention: EffectOutcome,
    calls_by_id: dict[str, dict[str, Any]],
) -> int:
    call = calls_by_id.get(intervention.call_id)
    if call is None or call["name"] != intervention.tool_name:
        raise ValueError("effect_outcome target does not match a recorded effect call")
    args_hash = Effect.hash_request(intervention.tool_name, dict(call.get("args", {})))
    matches = [effect for effect in ledger if effect.args_hash == args_hash]
    if len(matches) != 1:
        raise ValueError(f"effect_outcome expected one inherited effect, found {len(matches)}")
    matches[0].response = deepcopy(intervention.replacement)
    return matches[0].seq
