from __future__ import annotations

import json

from culprit_runner.persistence import json_bytes, json_line_bytes


def test_json_line_bytes_emits_exactly_one_parseable_line() -> None:
    payload = {"nested": {"value": 1}, "items": [1, 2]}
    rendered = json_line_bytes(payload)

    assert rendered.count(b"\n") == 1
    assert json.loads(rendered) == payload


def test_json_bytes_remains_pretty_for_regular_json_artifacts() -> None:
    rendered = json_bytes({"value": 1})

    assert b"\n  \"value\"" in rendered
    assert json.loads(rendered) == {"value": 1}
