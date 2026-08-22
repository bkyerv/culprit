# Progress

Status vocabulary: **verified**, **done but unverified**, **partial**, **blocked**, **not started**.

## P0 — Foundations and sandbox probe

| Deliverable | Status | Evidence |
|---|---|---|
| Blueprint read completely | verified | Read before any repository or cloud mutation on 2026-08-22. |
| Git repository initialized | verified | Local repository initialized on branch `main`. |
| Monorepo scaffold | verified | Required directories, uv workspace manifests, and lockfile exist. |
| Project-local uv environment | partial | `.venv` uses Python 3.14.4 and meets `>=3.12`; dependency install is still running. |
| Private GitHub repository | not started | — |
| Dedicated Google Cloud project | not started | — |
| Billing, tag, and APIs | not started | — |
| Firestore and Cloud Storage | not started | — |
| Service accounts and IAM | not started | — |
| Minimal runner deployed with sandbox launcher | not started | — |
| Sandbox CLI help captured | not started | — |
| Sandbox A → GCS → sandbox B round-trip | not started | P0 gate. |
| Successful report committed | not started | Expected at `docs/p0-probe-report.json`. |

No P1 work has started.
