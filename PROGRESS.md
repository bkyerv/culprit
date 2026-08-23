# Progress

Status vocabulary: **verified**, **done but unverified**, **partial**, **blocked**, **not started**.

## P0 — Foundations and sandbox probe

| Deliverable | Status | Evidence |
|---|---|---|
| Blueprint read completely | verified | Read before any repository or cloud mutation on 2026-08-22. |
| Git repository initialized | verified | Local repository initialized on branch `main`. |
| Monorepo scaffold | verified | Required directories, uv workspace manifests, and lockfile exist. |
| Project-local uv environment | verified | `UV_HTTP_TIMEOUT=120 uv sync --locked --all-packages` completed; runner imports and Ruff passes. |
| Private GitHub repository | verified | `https://github.com/bkyerv/culprit`, private, `main` at `a31a071`. |
| Dedicated Google Cloud project | verified | `culprit-6f973`, project number `859405737127`, created 2026-08-23. |
| Billing, tag, and APIs | partial | Billing, required APIs, label, and `$20`/`$50` alerts verified; exact Resource Manager tag blocked because no organization/tag key is visible. |
| Firestore and Cloud Storage | verified | Native database and lifecycle-managed `gs://culprit-6f973-state`, both `us-central1`. |
| Service accounts and IAM | partial | Runtime grants are least-privilege and verified; an unexpected concurrent user Token Creator binding on runner is preserved in `BLOCKERS.md`. |
| Minimal runner deployed with sandbox launcher | verified | Revision `culprit-runner-00001-npc` exports both `sandboxLauncher: true` and gen2. |
| Sandbox CLI help captured | verified | Six help commands exited 0; raw output in `docs/p0-probe-report.json`, summary in `docs/sandbox-cli-reference.md`. |
| Sandbox A → GCS → sandbox B round-trip | verified | Different B restored the GCS-downloaded tar and read the exact 117 source bytes; all hashes match. |
| Successful report artifact saved | verified | `docs/p0-probe-report.json` records `all_checks_passed: true`; repository commit/push is tracked separately from the gate. |

**Gate P0 is green and was reconciled against the deployed project on 2026-08-23.** The project,
runner deployment, and cross-sandbox round-trip are verified facts, not pending work. No P1 work has
started.
