from __future__ import annotations

from pathlib import Path

from culprit_core import load_scenario


def test_hero_scenario_is_data_and_declares_the_full_policy() -> None:
    scenario = load_scenario(Path("scenarios/supplier-counter-offer"))

    assert scenario.scenario_id == "supplier-counter-offer"
    assert {criterion.grader for criterion in scenario.criteria} == {"invariant", "rubric"}
    assert "send_email" in scenario.capability_policy.effect_permissions
    assert scenario.capability_policy.egress_policy == "deny"
    invariant = next(
        criterion
        for criterion in scenario.criteria
        if criterion.criterion_id == "no_internal_cost_disclosure"
    )
    assert invariant.config["predicate"] == "outbound_excludes_internal_derived_values"
    assert Path(
        "scenarios/supplier-counter-offer/policy/comms_policy.md"
    ).is_file()
    assert not Path(
        "scenarios/supplier-counter-offer/workspace/governance/communications/policies/"
        "supplier-data-handling.md"
    ).exists()
