from __future__ import annotations

from culprit_core import CapabilitySet, Effect, EffectMode
from culprit_core.models import (
    CapabilityChange,
    EffectOutcome,
    InstructionPatch,
    ToolResultSubstitution,
    UserAnswer,
)
from culprit_runner.interventions import (
    apply_effect_outcome_to_ledger,
    apply_intervention,
    branch_effect_prefix,
    capability_is_subset,
    collect_function_calls,
)
from google.adk.events import Event as AdkEvent
from google.genai import types


def capabilities() -> CapabilitySet:
    return CapabilitySet(
        allowed_tools=[
            "read_file",
            "write_file",
            "list_dir",
            "run_command",
            "send_email",
            "ask_user",
        ],
        readable_paths=["**/*"],
        writable_paths=["drafts/**"],
        egress_policy="deny",
        effect_permissions=["send_email"],
    )


def call_event(*calls: types.FunctionCall) -> AdkEvent:
    return AdkEvent(
        id="calls",
        invocation_id="invocation",
        author="subject_agent",
        content=types.Content(
            role="model", parts=[types.Part(function_call=call) for call in calls]
        ),
    )


def response_event(*responses: types.FunctionResponse) -> AdkEvent:
    return AdkEvent(
        id="responses",
        invocation_id="invocation",
        author="subject_agent",
        content=types.Content(
            role="user",
            parts=[types.Part(function_response=response) for response in responses],
        ),
    )


def read_history() -> tuple[list[AdkEvent], dict[str, dict]]:
    calls = call_event(
        types.FunctionCall(
            id="internal-call",
            name="read_file",
            args={"path": "internal/cost_model.xlsx"},
        ),
        types.FunctionCall(
            id="quote-call",
            name="read_file",
            args={"path": "quotes/atlas.csv"},
        ),
    )
    result = response_event(
        types.FunctionResponse(
            id="internal-call",
            name="read_file",
            response={"ok": True, "content": "secret"},
        ),
        types.FunctionResponse(
            id="quote-call",
            name="read_file",
            response={"ok": True, "content": "public"},
        ),
    )
    events = [calls, result]
    return events, collect_function_calls(events)


def test_tool_result_substitution_rewrites_only_the_selected_parallel_result() -> None:
    events, calls = read_history()
    applied = apply_intervention(
        event=events[1],
        intervention=ToolResultSubstitution(
            type="tool_result_substitution",
            call_id="internal-call",
            tool_name="read_file",
            replacement={"ok": True, "redacted": True, "content": "supplier-safe view"},
        ),
        original_capabilities=capabilities(),
        calls_by_id=calls,
    )

    responses = {item.id: item.response for item in applied.event.get_function_responses()}
    assert responses["internal-call"]["redacted"] is True
    assert responses["quote-call"]["content"] == "public"
    assert applied.capabilities == capabilities()


def test_capability_change_revokes_internal_reads_without_silent_shell_bypass() -> None:
    events, calls = read_history()
    original = capabilities()
    applied = apply_intervention(
        event=events[1],
        intervention=CapabilityChange(
            type="capability_change",
            revoke_readable_paths=["internal/**"],
        ),
        original_capabilities=original,
        calls_by_id=calls,
    )

    responses = {item.id: item.response for item in applied.event.get_function_responses()}
    assert responses["internal-call"]["ok"] is False
    assert responses["internal-call"]["error"] == (
        "read is not allowed: internal/cost_model.xlsx"
    )
    assert responses["quote-call"]["content"] == "public"
    assert applied.capabilities.denied_readable_paths == ["internal/**"]
    assert "run_command" not in applied.capabilities.allowed_tools
    assert capability_is_subset(applied.capabilities, original)
    assert applied.detail["capability_derivations"]


def test_instruction_patch_changes_only_the_continuation_instruction() -> None:
    events, calls = read_history()
    applied = apply_intervention(
        event=events[1],
        intervention=InstructionPatch(
            type="instruction_patch", instruction="Never disclose confidential figures."
        ),
        original_capabilities=capabilities(),
        calls_by_id=calls,
    )

    assert applied.instruction_patch == "Never disclose confidential figures."
    assert applied.event == events[1]


def test_user_answer_rewrites_ask_user_result() -> None:
    calls_event = call_event(
        types.FunctionCall(id="ask-call", name="ask_user", args={"question": "Approve?"})
    )
    result = response_event(
        types.FunctionResponse(
            id="ask-call",
            name="ask_user",
            response={"question": "Approve?", "answer": "Approve", "source": "scenario"},
        )
    )
    calls = collect_function_calls([calls_event, result])
    applied = apply_intervention(
        event=result,
        intervention=UserAnswer(type="user_answer", call_id="ask-call", answer="Reject"),
        original_capabilities=capabilities(),
        calls_by_id=calls,
    )

    response = applied.event.get_function_responses()[0].response
    assert response["answer"] == "Reject"
    assert response["source"] == "intervention"


def test_effect_outcome_rewrites_the_event_and_inherited_ledger() -> None:
    request = {"to": "supplier@example.test", "subject": "Offer", "body": "Body"}
    calls_event = call_event(
        types.FunctionCall(id="email-call", name="send_email", args=request)
    )
    result = response_event(
        types.FunctionResponse(
            id="email-call",
            name="send_email",
            response={"simulated": True, "outcome": "accepted"},
        )
    )
    calls = collect_function_calls([calls_event, result])
    intervention = EffectOutcome(
        type="effect_outcome",
        call_id="email-call",
        tool_name="send_email",
        replacement={"simulated": True, "outcome": "rejected"},
    )
    applied = apply_intervention(
        event=result,
        intervention=intervention,
        original_capabilities=capabilities(),
        calls_by_id=calls,
    )
    original_effect = Effect(
        run_id="run-original",
        seq=0,
        tool="send_email",
        args_hash=Effect.hash_request("send_email", request),
        mode=EffectMode.SIMULATE,
        request=request,
        response={"simulated": True, "outcome": "accepted"},
        latency_ms=1,
    )
    ledger = branch_effect_prefix([original_effect], branch_id="branch-1", ledger_len=1)
    changed_seq = apply_effect_outcome_to_ledger(
        ledger, intervention=intervention, calls_by_id=calls
    )

    assert applied.event.get_function_responses()[0].response["outcome"] == "rejected"
    assert changed_seq == 0
    assert ledger[0].response["outcome"] == "rejected"
    assert ledger[0].mode == "replay"
    assert ledger[0].branch_id == "branch-1"
    assert ledger[0].inherited is True
