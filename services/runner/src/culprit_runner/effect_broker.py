"""Safe, append-only mediation for every attempted external effect."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Protocol

from culprit_core.models import Effect, EffectMode
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

INPUT_USD_PER_MILLION_TOKENS = 0.75
OUTPUT_USD_PER_MILLION_TOKENS = 3.75


class WorldModelOutcome(BaseModel):
    outcome: Literal["accepted", "rejected", "deferred"]
    summary: str
    external_reference: str | None = None
    simulated_external_state: dict[str, Any] = Field(default_factory=dict)


class WorldModel(Protocol):
    async def simulate(self, tool: str, request: dict[str, Any]) -> dict[str, Any]: ...


class GeminiWorldModel:
    """Constrained Gemini call that predicts an effect outcome without performing it."""

    def __init__(self, *, project: str, location: str, model: str) -> None:
        self.model = model
        self.client = genai.Client(vertexai=True, project=project, location=location)

    async def simulate(self, tool: str, request: dict[str, Any]) -> dict[str, Any]:
        prompt = json.dumps({"effect_tool": tool, "hypothetical_request": request}, sort_keys=True)
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are Culprit's world model. Predict only the plausible immediate outcome of "
                    "the hypothetical external effect supplied as JSON. You have no tools and must "
                    "not perform, transmit, fetch, or schedule anything. Keep the result concise."
                ),
                response_mime_type="application/json",
                response_schema=WorldModelOutcome,
                temperature=0.2,
                max_output_tokens=512,
            ),
        )
        parsed = response.parsed
        if isinstance(parsed, WorldModelOutcome):
            outcome = parsed
        else:
            outcome = WorldModelOutcome.model_validate(parsed)
        usage = response.usage_metadata
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
            getattr(usage, "thoughts_token_count", 0) or 0
        )
        return {
            "simulated": True,
            "world_model": self.model,
            "world_model_token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": int(getattr(usage, "total_token_count", 0) or 0),
            },
            "world_model_cost_usd": round(
                (
                    input_tokens * INPUT_USD_PER_MILLION_TOKENS
                    + output_tokens * OUTPUT_USD_PER_MILLION_TOKENS
                )
                / 1_000_000,
                9,
            ),
            **outcome.model_dump(mode="json"),
        }


EffectSink = Callable[[Effect], Awaitable[None]]


class EffectBroker:
    """Record simulated effects and expose deterministic replay matching for P2."""

    def __init__(
        self,
        *,
        run_id: str,
        mode: EffectMode,
        world_model: WorldModel,
        effect_sink: EffectSink,
        replay_history: Sequence[Effect] = (),
    ) -> None:
        self.run_id = run_id
        self.mode = mode
        self.world_model = world_model
        self.effect_sink = effect_sink
        self.ledger: list[Effect] = []
        self._replay_by_hash = {effect.args_hash: effect for effect in replay_history}

    async def perform(self, tool: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.mode == EffectMode.RECORD:
            raise RuntimeError("record mode is disabled in the MVP")

        args_hash = Effect.hash_request(tool, request)
        started = time.perf_counter()
        novel = False
        if self.mode == EffectMode.REPLAY and args_hash in self._replay_by_hash:
            response = self._replay_by_hash[args_hash].response
        else:
            response = await self.world_model.simulate(tool, request)
            novel = self.mode == EffectMode.REPLAY

        effect = Effect(
            run_id=self.run_id,
            seq=len(self.ledger),
            tool=tool,
            args_hash=args_hash,
            mode=self.mode,
            novel=novel,
            request=request,
            response=response,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        self.ledger.append(effect)
        await self.effect_sink(effect)
        return response
