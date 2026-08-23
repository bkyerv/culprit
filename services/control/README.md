# culprit-control

The trusted FastAPI control plane serves the approved vanilla UI, reads persisted evidence from
Firestore and Cloud Storage, and enqueues all execution-plane work through Cloud Tasks. It contains
no sandbox driver and never executes subject code.

Every route, including static assets and the SSE stream, is protected by HTTP Basic Auth. The
credential pair is loaded directly from the `culprit-basic-auth` Secret Manager version named by
`CULPRIT_BASIC_AUTH_SECRET`; it is not accepted from a literal environment value.

Provision and deploy with:

```bash
export UV_HTTP_TIMEOUT=120
./infra/provision-control-auth.sh
./infra/deploy-control.sh
```

`web/mock.js` remains available for the draft's standalone offline loader. The HTTP entry point
uses `web/sse.js` and a Firestore-derived bootstrap snapshot by default.
