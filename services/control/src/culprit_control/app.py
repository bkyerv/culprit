from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from culprit_control.auth import (
    BasicAuthMiddleware,
    BasicCredentials,
    SecretManagerCredentialLoader,
)
from culprit_control.settings import Settings
from culprit_control.store import ControlStore
from culprit_control.task_queue import TaskQueue
from culprit_control.view_model import build_ui_snapshot

RUN_ID_PATTERN = re.compile(r"^run-[A-Za-z0-9-]+$")
INVESTIGATION_ID_PATTERN = re.compile(r"^inv-[A-Za-z0-9-]+$")
SCENARIO_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_ADVANCE_ATTEMPTS = 180
BRANCH_SPEND_CAP_USD = 0.15
INVESTIGATION_SPEND_CAP_USD = 0.60
WEB_DIR = Path(
    os.environ.get(
        "CULPRIT_WEB_DIR",
        str(Path(__file__).resolve().parents[2] / "web"),
    )
)


class StartRunRequest(BaseModel):
    scenario_id: str = Field(default="supplier-counter-offer", max_length=64)
    run_id: str | None = Field(default=None, max_length=80)


class StartInvestigationRequest(BaseModel):
    run_id: str = Field(max_length=80)
    investigation_id: str | None = Field(default=None, max_length=80)


class AdvanceInvestigationRequest(BaseModel):
    run_id: str = Field(max_length=80)
    attempt: int = Field(default=0, ge=0, le=MAX_ADVANCE_ATTEMPTS)


def _run_id() -> str:
    return f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _investigation_id() -> str:
    return f"inv-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _require_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=422, detail="invalid run id")
    return run_id


def _require_investigation_id(investigation_id: str) -> str:
    if not INVESTIGATION_ID_PATTERN.fullmatch(investigation_id):
        raise HTTPException(status_code=422, detail="invalid investigation id")
    return investigation_id


def _json_sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {data}\n\n"


def create_app(
    *,
    settings: Settings | None = None,
    store: ControlStore | None = None,
    task_queue: TaskQueue | None = None,
    credential_loader: Callable[[], BasicCredentials] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or ControlStore(
        project_id=settings.project_id,
        bucket_name=settings.bucket_name,
    )
    task_queue = task_queue or TaskQueue(settings)
    credential_loader = credential_loader or SecretManagerCredentialLoader(
        settings.auth_secret_version
    )

    app = FastAPI(
        title="Culprit trusted control plane",
        version="0.4.0",
        description=(
            "Reads persisted evidence and enqueues isolated runner work through Cloud Tasks. "
            "This service never executes subject code or launches sandboxes."
        ),
    )
    app.add_middleware(BasicAuthMiddleware, credential_loader=credential_loader)
    app.state.settings = settings
    app.state.store = store
    app.state.task_queue = task_queue
    app.state.credential_loader = credential_loader

    async def ui_bundle(run_id: str, preferred_investigation_id: str = "") -> dict[str, Any]:
        run_detail, runs, investigation = await asyncio.gather(
            asyncio.to_thread(store.get_run_detail, run_id),
            asyncio.to_thread(store.list_runs),
            asyncio.to_thread(
                store.latest_investigation,
                run_id,
                preferred_id=preferred_investigation_id,
            ),
        )
        return build_ui_snapshot(
            runs=runs,
            run_detail=run_detail,
            investigation_detail=investigation,
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(
        run: str | None = Query(default=None),
        investigation: str | None = Query(default=None),
    ) -> HTMLResponse:
        run_id = run or settings.default_run_id
        preferred_investigation_id = investigation or (
            settings.default_investigation_id if run_id == settings.default_run_id else ""
        )
        if not run_id:
            runs = await asyncio.to_thread(store.list_runs, 1)
            if not runs:
                raise HTTPException(status_code=404, detail="no recorded runs")
            run_id = str(runs[0]["run_id"])
        _require_run_id(run_id)
        if preferred_investigation_id:
            _require_investigation_id(preferred_investigation_id)
        try:
            snapshot = await ui_bundle(run_id, preferred_investigation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        bootstrap = json.dumps(jsonable_encoder(snapshot), ensure_ascii=False).replace(
            "<", "\\u003c"
        )
        html = html.replace(
            "<!-- CULPRIT_BOOTSTRAP -->",
            f"<script>window.CULPRIT_BOOTSTRAP = {bootstrap};</script>",
        )
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "plane": "trusted-control"}

    @app.get("/api/runs")
    async def list_runs(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        runs = await asyncio.to_thread(store.list_runs, limit)
        return {"runs": runs, "count": len(runs), "source": "firestore"}

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        _require_run_id(run_id)
        try:
            detail, investigations, ui = await asyncio.gather(
                asyncio.to_thread(store.get_run_detail, run_id),
                asyncio.to_thread(store.investigations_for_run, run_id),
                ui_bundle(run_id),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc
        return {
            **detail,
            "investigations": investigations,
            "ui": ui,
            "source": "firestore",
        }

    @app.get("/api/runs/{run_id}/stream")
    async def stream_run(run_id: str, request: Request) -> StreamingResponse:
        _require_run_id(run_id)

        async def events():
            previous = ""
            heartbeat = 0
            while True:
                if await request.is_disconnected():
                    return
                try:
                    fingerprint, state = await asyncio.to_thread(store.stream_state, run_id)
                    if fingerprint != previous:
                        previous = fingerprint
                        yield _json_sse(
                            "state_changed",
                            {
                                "type": "state_changed",
                                "run_id": run_id,
                                "state_version": fingerprint,
                                "state": state,
                            },
                        )
                    heartbeat += 1
                    if heartbeat >= max(1, round(15 / settings.stream_poll_seconds)):
                        heartbeat = 0
                        yield ": heartbeat\n\n"
                except Exception as exc:  # noqa: BLE001 - stream reports and retries read failures.
                    yield _json_sse(
                        "stream_error",
                        {"type": "stream_error", "message": type(exc).__name__},
                    )
                await asyncio.sleep(settings.stream_poll_seconds)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/investigations/{investigation_id}")
    async def investigation_detail(investigation_id: str) -> dict[str, Any]:
        _require_investigation_id(investigation_id)
        try:
            detail = await asyncio.to_thread(store.get_investigation, investigation_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"investigation not found: {investigation_id}",
            ) from exc
        return {**detail, "source": "firestore"}

    @app.get("/api/evalsets/{evalset_id}")
    async def download_evalset(evalset_id: str) -> Response:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", evalset_id):
            raise HTTPException(status_code=422, detail="invalid evalset id")
        try:
            document, payload = await asyncio.to_thread(store.get_evalset, evalset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evalset not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        filename = f"{evalset_id}.evalset.json"
        return Response(
            payload,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Culprit-Derived-From": json.dumps(document.get("derived_from", {})),
            },
        )

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    async def start_run(body: StartRunRequest) -> dict[str, Any]:
        if not SCENARIO_PATTERN.fullmatch(body.scenario_id):
            raise HTTPException(status_code=422, detail="invalid scenario id")
        run_id = body.run_id or _run_id()
        _require_run_id(run_id)
        task_name = await asyncio.to_thread(
            task_queue.enqueue_runner,
            path="/runs",
            payload={"scenario_id": body.scenario_id, "run_id": run_id},
            task_id=f"record-{run_id}",
        )
        return {
            "run_id": run_id,
            "status": "queued",
            "task": task_name,
            "stream": f"/api/runs/{run_id}/stream",
        }

    @app.post("/api/investigations", status_code=status.HTTP_202_ACCEPTED)
    async def start_investigation(body: StartInvestigationRequest) -> dict[str, Any]:
        run_id = _require_run_id(body.run_id)
        investigation_id = body.investigation_id or _investigation_id()
        _require_investigation_id(investigation_id)
        try:
            run = await asyncio.to_thread(store.get_run_document, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc
        if run.get("status") != "completed" or run.get("verdict") != "fail":
            raise HTTPException(
                status_code=409,
                detail="only completed failed runs can be investigated",
            )
        analysis_task = await asyncio.to_thread(
            task_queue.enqueue_runner,
            path=f"/runs/{run_id}/investigations",
            payload={
                "investigation_id": investigation_id,
                "spend_cap_usd": INVESTIGATION_SPEND_CAP_USD,
            },
            task_id=f"analysis-{investigation_id}",
        )
        advance_task = await asyncio.to_thread(
            task_queue.enqueue_advance,
            investigation_id=investigation_id,
            run_id=run_id,
            attempt=0,
            credentials=credential_loader(),
        )
        return {
            "investigation_id": investigation_id,
            "run_id": run_id,
            "status": "queued",
            "analysis_task": analysis_task,
            "advance_task": advance_task,
            "spend_cap_usd": INVESTIGATION_SPEND_CAP_USD,
            "max_branches": 3,
        }

    @app.post("/api/internal/investigations/{investigation_id}/advance", include_in_schema=False)
    async def advance_investigation(
        investigation_id: str, body: AdvanceInvestigationRequest
    ) -> dict[str, Any]:
        _require_investigation_id(investigation_id)
        run_id = _require_run_id(body.run_id)
        if body.attempt >= MAX_ADVANCE_ATTEMPTS:
            await asyncio.to_thread(
                store.merge_investigation,
                investigation_id,
                {
                    "status": "failed",
                    "error": {
                        "type": "ControlPlaneTimeout",
                        "message": "investigation orchestration exceeded its bounded poll window",
                    },
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            return {"status": "failed", "reason": "bounded poll window exhausted"}
        try:
            investigation = await asyncio.to_thread(
                store.get_investigation_document, investigation_id
            )
        except KeyError:
            investigation = {}
        current_status = str(investigation.get("status") or "analysis_queued")
        if investigation and investigation.get("run_id") != run_id:
            raise HTTPException(status_code=409, detail="investigation run id changed")

        enqueued: list[str] = []
        if current_status == "branching":
            planned = sorted(
                investigation.get("planned_branches", []),
                key=lambda item: item.get("rank", 0),
            )
            if len(planned) == 3:
                for item in planned:
                    branch_id = str(item["branch_id"])
                    task_name = await asyncio.to_thread(
                        task_queue.enqueue_runner,
                        path=f"/runs/{run_id}/forks",
                        payload={
                            "fork_seq": item["fork_seq"],
                            "intervention": item["intervention"],
                            "investigation_id": investigation_id,
                            "branch_id": branch_id,
                            "branch_spend_cap_usd": BRANCH_SPEND_CAP_USD,
                            "investigation_spend_cap_usd": INVESTIGATION_SPEND_CAP_USD,
                        },
                        task_id=f"fork-{branch_id}",
                    )
                    enqueued.append(task_name)
        elif current_status == "awaiting_judge":
            detail = await asyncio.to_thread(store.get_investigation, investigation_id)
            terminal = [item.get("branch", {}) for item in detail["branches"]]
            failed = [
                branch.get("branch_id")
                for branch in terminal
                if branch.get("status") in {"failed", "aborted"}
            ]
            if failed:
                await asyncio.to_thread(
                    store.merge_investigation,
                    investigation_id,
                    {
                        "status": "failed",
                        "error": {
                            "type": "BranchFailure",
                            "message": f"terminal branches failed: {failed}",
                        },
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                )
                return {"status": "failed", "failed_branches": failed}
            judge_task = await asyncio.to_thread(
                task_queue.enqueue_runner,
                path=f"/investigations/{investigation_id}/judge",
                payload={},
                task_id=f"judge-{investigation_id}",
            )
            enqueued.append(judge_task)
        if current_status in {"completed", "failed"}:
            return {"status": current_status, "attempt": body.attempt, "enqueued": enqueued}
        next_task = await asyncio.to_thread(
            task_queue.enqueue_advance,
            investigation_id=investigation_id,
            run_id=run_id,
            attempt=body.attempt + 1,
            credentials=credential_loader(),
        )
        return {
            "status": current_status,
            "attempt": body.attempt,
            "enqueued": enqueued,
            "next_task": next_task,
        }

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return app


app = create_app()
