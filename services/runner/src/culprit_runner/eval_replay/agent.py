"""Deterministically replay a native EvalSet path through ADK's evaluator."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.evaluation.eval_case import IntermediateData
from google.adk.evaluation.eval_set import EvalSet
from google.adk.events import Event
from google.genai import types


class EvalPathReplayAgent(BaseAgent):
    evalset_path: str

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        evalset = EvalSet.model_validate_json(Path(self.evalset_path).read_bytes())
        invocation = (evalset.eval_cases[0].conversation or [])[0]
        intermediate = invocation.intermediate_data
        if isinstance(intermediate, IntermediateData):
            responses = {response.id: response for response in intermediate.tool_responses}
            for call in intermediate.tool_uses:
                yield Event(
                    invocation_id=ctx.invocation_id,
                    author=self.name,
                    content=types.Content(
                        role="model", parts=[types.Part(function_call=call)]
                    ),
                )
                if response := responses.get(call.id):
                    yield Event(
                        invocation_id=ctx.invocation_id,
                        author=self.name,
                        content=types.Content(
                            role="user", parts=[types.Part(function_response=response)]
                        ),
                    )
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=invocation.final_response,
        )


_evalset_path = os.environ.get("CULPRIT_EVALSET_PATH")
if not _evalset_path:
    # Import remains possible for discovery; generated pytest sets this before evaluation.
    _evalset_path = "missing.evalset.json"

root_agent = EvalPathReplayAgent(
    name="culprit_winning_path_replay",
    description="Replays the measured path encoded in a native ADK EvalSet.",
    evalset_path=_evalset_path,
)
