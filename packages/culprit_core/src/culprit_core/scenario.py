"""Load scenario data without coupling scenarios to the execution engine."""

from __future__ import annotations

from pathlib import Path

import yaml

from culprit_core.models import Scenario


def load_scenario(scenario_dir: Path) -> Scenario:
    config_path = scenario_dir / "scenario.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"scenario config does not exist: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"scenario config must be a mapping: {config_path}")
    scenario = Scenario.model_validate(raw)
    seed_dir = scenario_dir / scenario.workspace_seed
    if not seed_dir.is_dir():
        raise FileNotFoundError(f"workspace seed does not exist: {seed_dir}")
    scenario_root = scenario_dir.resolve()
    for criterion in scenario.criteria:
        policy_reference = criterion.config.get("policy_reference")
        if not policy_reference:
            continue
        policy_path = (scenario_dir / str(policy_reference)).resolve()
        if not policy_path.is_relative_to(scenario_root) or not policy_path.is_file():
            raise FileNotFoundError(
                f"criterion policy reference does not exist: {policy_reference}"
            )
    return scenario
