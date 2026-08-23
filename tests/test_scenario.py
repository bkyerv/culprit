from __future__ import annotations

from pathlib import Path

from culprit_core import load_scenario


def test_hero_scenario_is_data_and_declares_the_full_policy() -> None:
    scenario = load_scenario(Path("scenarios/supplier-counter-offer"))

    assert scenario.scenario_id == "supplier-counter-offer"
    assert {criterion.grader for criterion in scenario.criteria} == {"invariant", "rubric"}
    assert "send_email" in scenario.capability_policy.effect_permissions
    assert scenario.capability_policy.egress_policy == "deny"
