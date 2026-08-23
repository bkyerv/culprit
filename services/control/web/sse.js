// Production data adapter. The server injects a Firestore-derived snapshot
// before this module loads; every subsequent persisted change is refreshed
// through the authenticated REST endpoint.
export function createDataSource() {
  const snapshot = window.CULPRIT_BOOTSTRAP;
  if (!snapshot?.run?.id || snapshot.source !== "firestore") {
    throw new Error("Culprit live mode requires a Firestore bootstrap snapshot");
  }

  const listeners = new Set();
  const runId = encodeURIComponent(snapshot.run.id);
  const stream = new EventSource(`/api/runs/${runId}/stream`);
  const publish = (event) => listeners.forEach((listener) => listener(event));
  let refreshPromise = null;

  async function refreshSnapshot() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = fetch(`/api/runs/${runId}`, { credentials: "same-origin", cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json();
      })
      .then((payload) => publish({ type: "snapshot_update", snapshot: payload.ui }))
      .catch((error) => publish({ type: "stream_error", message: `Live refresh failed · ${error.message}` }))
      .finally(() => { refreshPromise = null; });
    return refreshPromise;
  }

  const forward = (message) => {
    try {
      const event = JSON.parse(message.data);
      publish(event.type ? event : { ...event, type: message.type });
    } catch {
      publish({ type: "stream_error", message: "Malformed SSE event" });
    }
  };

  stream.addEventListener("state_changed", (message) => {
    forward(message);
    refreshSnapshot();
  });
  stream.addEventListener("stream_error", forward);
  stream.onerror = () => publish({ type: "stream_error", message: "Live stream disconnected; reconnecting" });
  stream.onopen = () => publish({ type: "stream_connected" });

  async function command(path, body = {}) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `${response.status} ${response.statusText}`);
    }
    return response.status === 204 ? {} : response.json();
  }

  return {
    autoReplay: false,
    getSnapshot() { return snapshot; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    replay() { return command("/api/investigations", { run_id: snapshot.run.id }); },
    fork() { return Promise.reject(new Error("Manual forks require an explicit intervention; start an autonomous investigation instead")); },
    close() { stream.close(); listeners.clear(); },
  };
}
