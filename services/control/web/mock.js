import "./mock-data.js";

// The UI depends only on this factory. Replace this import/export with an SSE
// adapter to connect a live `/api/runs/{runId}/stream` endpoint.
export const createDataSource = window.CulpritMockData.createDataSource;
