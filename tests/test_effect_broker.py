from __future__ import annotations

import asyncio
from typing import Any

from culprit_core.models import Effect, EffectMode
from culprit_runner.effect_broker import EffectBroker


class FakeWorldModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def simulate(self, tool: str, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, request))
        return {"simulated": True, "outcome": "accepted"}


def test_simulate_records_effect_without_external_executor() -> None:
    async def exercise() -> None:
        persisted: list[Effect] = []
        world = FakeWorldModel()

        async def sink(effect: Effect) -> None:
            persisted.append(effect)

        broker = EffectBroker(
            run_id="run-1",
            mode=EffectMode.SIMULATE,
            world_model=world,
            effect_sink=sink,
        )
        response = await broker.perform(
            "send_email", {"to": "supplier@example.test", "body": "Offer"}
        )

        assert response["simulated"] is True
        assert len(world.calls) == 1
        assert persisted == broker.ledger
        assert persisted[0].mode == "simulate"
        assert persisted[0].novel is False

    asyncio.run(exercise())


def test_replay_match_is_deterministic_and_new_request_is_novel() -> None:
    async def exercise() -> None:
        request = {"to": "supplier@example.test", "body": "Original"}
        history = Effect(
            run_id="original",
            seq=0,
            tool="send_email",
            args_hash=Effect.hash_request("send_email", request),
            mode=EffectMode.SIMULATE,
            request=request,
            response={"simulated": True, "outcome": "accepted", "id": "original"},
            latency_ms=1,
        )
        persisted: list[Effect] = []
        world = FakeWorldModel()

        async def sink(effect: Effect) -> None:
            persisted.append(effect)

        broker = EffectBroker(
            run_id="branch",
            mode=EffectMode.REPLAY,
            world_model=world,
            effect_sink=sink,
            replay_history=[history],
        )
        matched = await broker.perform("send_email", request)
        novel = await broker.perform(
            "send_email", {"to": "supplier@example.test", "body": "Changed"}
        )

        assert matched["id"] == "original"
        assert persisted[0].novel is False
        assert novel["simulated"] is True
        assert persisted[1].novel is True
        assert len(world.calls) == 1

    asyncio.run(exercise())
