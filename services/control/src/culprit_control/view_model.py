from __future__ import annotations

import json
from datetime import datetime
from typing import Any

SAFETY_ID = "no_internal_cost_disclosure"
QUALITY_ID = "persuasive_specific_counter_offer"
COMPLETENESS_ID = "one_message_per_supplier"


def _truncate(value: Any, limit: int = 1_600) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 1] + "…"


def _elapsed(started: Any, completed: Any) -> str:
    if not started or not completed:
        return "running"
    try:
        start = datetime.fromisoformat(str(started))
        end = datetime.fromisoformat(str(completed))
        seconds = max(0.0, (end - start).total_seconds())
        return f"{seconds:.1f} s"
    except ValueError:
        return "—"


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    verdict = str(run.get("verdict") or run.get("status") or "queued").lower()
    status = "fail" if verdict == "fail" else "pass" if verdict == "pass" else "running"
    return {
        "id": str(run.get("run_id", "")),
        "title": "Supplier counter-offer"
        if run.get("scenario_id") == "supplier-counter-offer"
        else str(run.get("scenario_id") or "Scenario run"),
        "status": status,
        "elapsed": _elapsed(run.get("started_at"), run.get("completed_at")),
        "verdict": verdict.upper(),
    }


def _event_parts(event: dict[str, Any]) -> list[dict[str, Any]]:
    payload = event.get("payload") or {}
    content = payload.get("content") or {}
    return [part for part in content.get("parts", []) if isinstance(part, dict)]


def _event_view(
    event: dict[str, Any], *, culprit_seq: int | None, effect_id: str | None
) -> dict[str, Any]:
    calls = [part["function_call"] for part in _event_parts(event) if "function_call" in part]
    responses = [
        part["function_response"] for part in _event_parts(event) if "function_response" in part
    ]
    texts = [part["text"] for part in _event_parts(event) if "text" in part]
    kind = str(event.get("kind", "event"))
    if calls:
        names = [str(call.get("name", "tool")) for call in calls]
        name = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"
        args = "\n".join(_truncate(call.get("args", {}), 700) for call in calls)
        summary = ", ".join(names)
        result = "tool call recorded"
    elif responses:
        names = [str(response.get("name", "tool")) for response in responses]
        name = names[0] if len(names) == 1 else f"{names[0]} results"
        args = "\n".join(
            _truncate(response.get("response", {}).get("path") or response.get("id") or "", 250)
            for response in responses
        )
        result = "\n".join(_truncate(response.get("response", {}), 900) for response in responses)
        summary = ", ".join(names)
    elif texts:
        name = "final_response"
        args = ""
        result = _truncate("\n".join(str(text) for text in texts))
        summary = "subject agent completed"
    else:
        name = kind
        args = _truncate(event.get("payload", {}), 700)
        result = "recorded"
        summary = kind.replace("_", " ")

    seq = int(event.get("seq", 0))
    names = [str(call.get("name")) for call in calls]
    status = "culprit" if seq == culprit_seq else "warn" if "send_email" in names else "pass"
    capabilities = event.get("capability_set") or {}
    capability_lines = [
        *(f"tool · {tool}" for tool in capabilities.get("allowed_tools", [])),
        *(f"read · {path}" for path in capabilities.get("readable_paths", [])),
        *(f"effect · {item}" for item in capabilities.get("effect_permissions", [])),
        f"egress · {capabilities.get('egress_policy', 'deny')}",
    ]
    usage = event.get("token_usage") or {}
    model = (event.get("payload") or {}).get("model_version") or "gemini-3.7-flash"
    view = {
        "seq": seq,
        "kind": kind,
        "name": name,
        "summary": summary,
        "status": status,
        "role": str(event.get("role", "—")),
        "model": str(model),
        "tokens": str(usage.get("total_tokens", "—")),
        "latency": f"{float(event.get('latency_ms', 0)):.0f} ms",
        "args": args,
        "result": result,
        "capabilities": capability_lines or ["recorded capability set unavailable"],
    }
    if status == "culprit":
        view["causal"] = (
            "AnalystAgent ranked this internal cost-model result first. Every planned "
            "counterfactual forks at this persisted boundary."
        )
    if effect_id:
        view["effectId"] = effect_id
    return view


def _grade_map(grades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(grade.get("criterion_id")): grade for grade in grades}


def _quality_value(grades: dict[str, dict[str, Any]]) -> str:
    grade = grades.get(QUALITY_ID, {})
    score = (grade.get("detail") or {}).get("score")
    if score is None:
        return "pass" if grade.get("passed") else "fail"
    return f"{float(score):.1f} · {'pass' if grade.get('passed') else 'fail'}"


def _criterion_value(grades: dict[str, dict[str, Any]], criterion_id: str) -> str:
    grade = grades.get(criterion_id)
    if not grade:
        return "—"
    return "pass" if grade.get("passed") else "fail"


def _intervention_label(intervention: dict[str, Any]) -> tuple[str, str, str]:
    kind = str(intervention.get("type") or "intervention")
    if kind == "capability_change":
        revoked = ", ".join(intervention.get("revoke_readable_paths", [])) or "restricted data"
        return "revoke", "revoke internal reads", f"capability − reads on {revoked}"
    if kind == "tool_result_substitution":
        return "substitute", "substitute tool result", "supplier-safe result at causal boundary"
    if kind == "instruction_patch":
        return "instruct", "patch continuation instruction", "explicit non-disclosure constraint"
    return kind, kind.replace("_", " "), kind.replace("_", " ")


def _branch_view(
    planned: dict[str, Any],
    branch_detail: dict[str, Any],
    evidence: dict[str, Any],
    winner_id: str,
) -> dict[str, Any]:
    branch = branch_detail.get("branch") or {}
    branch_id = str(planned.get("branch_id") or branch.get("branch_id") or "")
    rank = int(planned.get("rank") or 0)
    intervention = planned.get("intervention") or branch.get("intervention") or {}
    short_label, label, change = _intervention_label(intervention)
    grades = _grade_map(branch_detail.get("grades", []))
    status = str(branch.get("status") or "queued")
    all_pass = bool(evidence.get("all_criteria_passed"))
    if status == "completed":
        live_status = "winner" if branch_id == winner_id else "passed" if all_pass else "failed"
        progress = 100
        live_detail = "all criteria passed" if all_pass else "one or more criteria failed"
    elif status in {"failed", "aborted"}:
        live_status, progress, live_detail = "failed", 100, "branch execution failed"
    elif status == "running":
        live_status, progress, live_detail = "running", 55, "re-executing in isolated sandbox"
    else:
        live_status, progress, live_detail = "queued", 0, "waiting for isolated Cloud Run sandbox"
    capabilities = (
        evidence.get("effective_capabilities") or branch.get("effective_capabilities") or {}
    )
    denied = capabilities.get("denied_readable_paths", [])
    removed_tools = sorted(
        set((branch.get("original_capabilities") or {}).get("allowed_tools", []))
        - set(capabilities.get("allowed_tools", []))
    )
    capability_delta = (
        ", ".join([*(f"− {path}" for path in denied), *(f"− {tool}" for tool in removed_tools)])
        or "none"
    )
    duration_ms = float(evidence.get("duration_ms") or branch.get("duration_ms") or 0)
    cost = float(evidence.get("cost_usd") or branch.get("accounted_spend_usd") or 0)
    quality = _quality_value(grades)
    quality_score = quality.split(" · ", 1)[0] if " · " in quality else "—"
    return {
        "id": f"r{rank}" if rank else branch_id[-2:],
        "branchId": branch_id,
        "shortLabel": short_label,
        "label": label,
        "change": change,
        "capabilityDelta": capability_delta,
        "changeSize": f"{int(evidence.get('change_size') or 0)} bytes",
        "safety": _criterion_value(grades, SAFETY_ID),
        "quality": "pass" if grades.get(QUALITY_ID, {}).get("passed") else "fail",
        "qualityScore": quality_score,
        "complete": _criterion_value(grades, COMPLETENESS_ID),
        "cost": f"${cost:.4f}",
        "elapsed": f"{duration_ms / 1000:.1f} s" if duration_ms else "running",
        "sandbox": branch.get("execution_sandbox_name") or "isolated Cloud Run",
        "effects": f"{int(branch.get('novel_effect_count') or 0)} / {int(branch.get('effect_count') or 0)} · novel",
        "note": "Winner · fewest capabilities and smallest passing change"
        if branch_id == winner_id
        else "All criteria passed"
        if all_pass
        else "Measured criterion failure",
        "finalStatus": "winner" if branch_id == winner_id else "pass" if all_pass else "fail",
        "liveStatus": live_status,
        "liveDetail": live_detail,
        "progress": progress,
        "rank": rank,
    }


def _effect_view(
    effect: dict[str, Any], *, branch_key: str, effect_id: str, safety_passed: bool
) -> dict[str, Any]:
    request = effect.get("request") or {}
    response = effect.get("response") or {}
    target = request.get("to") or request.get("url") or "brokered target"
    return {
        "id": effect_id,
        "branch": branch_key,
        "at": str(effect.get("created_at") or "—").replace("T", " ")[:19],
        "action": str(effect.get("tool") or "effect"),
        "target": str(target),
        "mode": str(effect.get("mode") or "simulate"),
        "novel": bool(effect.get("novel")),
        "status": "captured" if safety_passed else "disclosed",
        "args": _truncate(request, 5_000),
        "response": _truncate(response, 2_000),
    }


def _email_lines(effect: dict[str, Any]) -> list[str]:
    body = str((effect.get("request") or {}).get("body") or "No email body recorded.")
    return [line.strip() for line in body.splitlines() if line.strip()]


def build_ui_snapshot(
    *,
    runs: list[dict[str, Any]],
    run_detail: dict[str, Any],
    investigation_detail: dict[str, Any] | None,
) -> dict[str, Any]:
    run = run_detail["run"]
    investigation = (investigation_detail or {}).get("investigation") or {}
    ranking = investigation.get("ranking") or {}
    candidates = ranking.get("candidates") or []
    culprit_seq = int(candidates[0]["event_seq"]) if candidates else None

    source_grades = _grade_map(run_detail.get("grades", []))
    source_safety_passed = bool(source_grades.get(SAFETY_ID, {}).get("passed"))
    source_effects = run_detail.get("effects", [])
    source_effect_views = [
        _effect_view(
            effect,
            branch_key="original",
            effect_id=f"original_{int(effect.get('seq', index)):03d}",
            safety_passed=source_safety_passed,
        )
        for index, effect in enumerate(source_effects)
    ]

    send_call_index = 0
    trace = []
    for event in run_detail.get("events", []):
        calls = [
            part.get("function_call", {})
            for part in _event_parts(event)
            if part.get("function_call", {}).get("name") == "send_email"
        ]
        effect_id = None
        if calls and send_call_index < len(source_effect_views):
            effect_id = source_effect_views[send_call_index]["id"]
            send_call_index += 1
        trace.append(_event_view(event, culprit_seq=culprit_seq, effect_id=effect_id))

    branch_details = {
        str(item.get("branch", {}).get("branch_id")): item
        for item in (investigation_detail or {}).get("branches", [])
    }
    evidence_by_id = {
        str(item.get("branch_id")): item
        for item in investigation.get("measured_branch_evidence", [])
    }
    winner_id = str(investigation.get("winner") or "")
    planned = sorted(
        investigation.get("planned_branches", []), key=lambda item: item.get("rank", 0)
    )
    branches = [
        _branch_view(
            item,
            branch_details.get(str(item.get("branch_id")), {}),
            evidence_by_id.get(str(item.get("branch_id")), {}),
            winner_id,
        )
        for item in planned
    ]
    branch_by_id = {branch["branchId"]: branch for branch in branches}

    effects = list(source_effect_views)
    for branch_id, detail in branch_details.items():
        branch = branch_by_id.get(branch_id)
        if not branch:
            continue
        safety_passed = branch["safety"] == "pass"
        for index, effect in enumerate(detail.get("effects", [])):
            effects.append(
                _effect_view(
                    effect,
                    branch_key=branch["id"],
                    effect_id=f"{branch['id']}_{int(effect.get('seq', index)):03d}",
                    safety_passed=safety_passed,
                )
            )

    criteria = []
    criterion_labels = {
        SAFETY_ID: "Safety invariant",
        QUALITY_ID: "Quality rubric",
        COMPLETENESS_ID: "One email per recipient",
    }
    for criterion_id in (SAFETY_ID, QUALITY_ID, COMPLETENESS_ID):
        row = {
            "id": criterion_id,
            "label": criterion_labels[criterion_id],
            "original": _quality_value(source_grades)
            if criterion_id == QUALITY_ID
            else _criterion_value(source_grades, criterion_id),
        }
        for branch in branches:
            if criterion_id == QUALITY_ID:
                row[branch["id"]] = f"{branch['qualityScore']} · {branch['quality']}"
            else:
                row[branch["id"]] = branch["safety" if criterion_id == SAFETY_ID else "complete"]
        criteria.append(row)
    metric_rows = [
        ("effects", "Novel effects", lambda branch: branch["effects"]),
        (
            "capability",
            "Capability delta · smaller is better",
            lambda branch: branch["capabilityDelta"],
        ),
        ("size", "Change size · smaller is better", lambda branch: branch["changeSize"]),
        ("cost", "Accounted cost · lower is better", lambda branch: branch["cost"]),
        ("duration", "Duration · lower is better", lambda branch: branch["elapsed"]),
    ]
    for criterion_id, label, getter in metric_rows:
        row = {"id": criterion_id, "label": label, "original": "—"}
        row.update({branch["id"]: getter(branch) for branch in branches})
        criteria.append(row)

    winner_branch = branch_by_id.get(winner_id)
    winner_effects = branch_details.get(winner_id, {}).get("effects", [])
    emails = []
    for source_effect in source_effects:
        target = (source_effect.get("request") or {}).get("to")
        matching = next(
            (
                effect
                for effect in winner_effects
                if (effect.get("request") or {}).get("to") == target
            ),
            None,
        )
        if matching:
            supplier = "Atlas" if "atlas" in str(target).lower() else "Beacon"
            emails.append(
                {
                    "supplier": supplier,
                    "target": str(target),
                    "original": _email_lines(source_effect),
                    "winner": _email_lines(matching),
                }
            )

    violations = (source_grades.get(SAFETY_ID, {}).get("detail") or {}).get("violations", [])
    run_view = _run_summary(run)
    run_view["task"] = str(run.get("task") or "")
    winner_rank = next(
        (
            item
            for item in (investigation.get("verdict") or {}).get("ranked_branches", [])
            if item.get("branch_id") == winner_id
        ),
        {},
    )
    return {
        "source": "firestore",
        "sourceLabel": "LIVE FIRESTORE",
        "sourceState": "SSE",
        "run": run_view,
        "runs": [_run_summary(item) for item in runs],
        "failure": {
            "title": "Internal cost data disclosed to both suppliers",
            "detail": (
                f"Safety failed with {len(violations)} measured disclosures. "
                f"Email quality {_quality_value(source_grades)}, and one-email-per-recipient "
                f"{_criterion_value(source_grades, COMPLETENESS_ID)}."
            ),
        },
        "prediction": {
            "title": "Revoking internal reads would destroy email quality.",
            "result": (
                "False. The source and both passing counterfactuals scored 1.0; "
                "the unchanged rubric may be insufficiently sensitive."
            ),
        },
        "candidates": [
            {
                "rank": int(candidate.get("rank", index + 1)),
                "seq": int(candidate.get("event_seq", 0)),
                "score": float(candidate.get("culpability_score", 0)),
                "summary": str(candidate.get("summary") or candidate.get("event_kind") or "event"),
            }
            for index, candidate in enumerate(candidates)
        ],
        "trace": trace,
        "branches": branches,
        "criteria": criteria,
        "effects": effects,
        "emails": emails,
        "investigation": {
            "id": investigation.get("investigation_id"),
            "status": investigation.get("status") or "not_started",
            "winner": winner_id or None,
            "evidence": investigation.get("evidence") or "Investigation has not completed.",
            "evalsetId": investigation.get("evalset_id"),
        },
        "outcome": {
            "winnerLabel": winner_branch["shortLabel"] if winner_branch else "pending",
            "winnerIndex": winner_branch["id"] if winner_branch else "—",
            "elapsed": winner_branch["elapsed"] if winner_branch else "—",
            "cost": winner_branch["cost"] if winner_branch else "—",
            "capabilityDelta": winner_branch["capabilityDelta"] if winner_branch else "—",
            "changeSize": winner_branch["changeSize"] if winner_branch else "—",
            "rankRationale": winner_rank.get("rationale")
            or investigation.get("evidence")
            or "Pending executed evidence.",
        },
    }
