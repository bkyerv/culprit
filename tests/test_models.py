from __future__ import annotations

from types import SimpleNamespace

import pytest
from culprit_core.models import CapabilitySet, Event, TokenUsage
from culprit_runner.recorder import _cost_usd, _token_usage
from pydantic import ValidationError


def test_every_event_requires_a_capability_set() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(
            {
                "run_id": "run-1",
                "seq": 0,
                "event_id": "event-1",
                "role": "model",
                "kind": "model_event",
            }
        )


def test_capability_entries_are_not_duplicated() -> None:
    with pytest.raises(ValidationError):
        CapabilitySet(allowed_tools=["read_file", "read_file"])


def test_gemini_37_flash_cost_is_recorded_from_token_usage() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000)

    assert _cost_usd(usage) == 4.5


def test_reasoning_tokens_are_counted_as_billable_output() -> None:
    event = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=20,
            thoughts_token_count=30,
            total_token_count=150,
        )
    )

    assert _token_usage(event) == TokenUsage(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
    )
