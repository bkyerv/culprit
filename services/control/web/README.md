# Culprit UI draft

Culprit is a self-contained static investigation interface. Open `index.html` directly to run the recorded supplier-email investigation; there is no install, build, server, or network dependency.

## Structure

- `index.html` — document shell and the direct-`file://` loader.
- `style.css` — the complete dark instrument-style design system and responsive layouts.
- `app.js` — the HTTP entry point. It selects the live SSE adapter and boots the UI.
- `ui.js` — rendering, hash routing, keyboard controls, inspector, branch race, diffs, effects, and criteria.
- `mock.js` — the interchangeable ES-module mock adapter for offline demos.
- `mock-data.js` — the recorded investigation and timed replay. It is also exposed as a small browser global so `index.html` can run directly despite browsers blocking external ES-module imports from `file://` origins.
- `sse.js` — a ready production adapter for the FastAPI SSE stream.
- `screenshots/` — verified captures of the branch race and criteria grid.

The UI only knows this data-source interface:

```js
{
  autoReplay,                // true only for the standalone mock demo
  getSnapshot(),             // synchronous initial investigation snapshot
  subscribe(listener),       // emits race_reset and branch_update events
  replay(),                  // starts an investigation; may return a Promise
  fork(seq),                 // returns Promise<{ branchId, checkpoint }>
  close()
}
```

## Live SSE mode

1. In the FastAPI-served HTML, place the initial run snapshot on `window.CULPRIT_BOOTSTRAP` before `app.js` loads. It must have the same top-level shape as the object in `mock-data.js`: `run`, `runs`, `failure`, `trace`, `candidates`, `branches`, `criteria`, `effects`, and `emails`.

   ```html
   <script>
     window.CULPRIT_BOOTSTRAP = {{ run_snapshot_json | safe }};
   </script>
   ```

2. `app.js` imports `sse.js` by default. To serve an HTTP mock demo instead, change exactly one
   import:

   ```diff
   - import { createDataSource } from "./sse.js";
   + import { createDataSource } from "./mock.js";
   ```

3. Emit JSON events from `/api/runs/{runId}/stream`. Either unnamed SSE messages with a `type` field, or named `race_reset` / `branch_update` events, work. Branch updates use this shape:

   ```json
   {
     "type": "branch_update",
     "id": "c",
     "status": "running",
     "detail": "executing redacted tool result",
     "progress": 50
   }
   ```

4. The adapter starts autonomous investigations through `POST /api/investigations`. Manual fork
   buttons fail honestly until an explicit intervention editor is added; the control service does
   not invent an intervention from only an event number.

`EventSource` uses the browser's cached per-origin HTTP Basic Auth credentials automatically. The command requests use same-origin credentials as well.

## Navigation

Use `j` / `k` to move through trace events, `f` to fork, `/` to filter, `Esc` to close the active panel, `1`–`7` to change views, and `?` for the complete key map. Event links use `#/run/{id}/event/{seq}`; view links use `#/run/{id}/view/{view}`.
