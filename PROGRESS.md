# Progress

Status vocabulary: **verified**, **done but unverified**, **partial**, **blocked**, **not started**.

## P0 — Foundations and sandbox probe

| Deliverable | Status | Evidence |
|---|---|---|
| Blueprint read completely | verified | Read before any repository or cloud mutation on 2026-08-22. |
| Git repository initialized | verified | Local repository initialized on branch `main`. |
| Monorepo scaffold | verified | Required directories, uv workspace manifests, and lockfile exist. |
| Project-local uv environment | verified | `UV_HTTP_TIMEOUT=120 uv sync --locked --all-packages` completed; runner imports and Ruff passes. |
| Private GitHub repository | verified | `https://github.com/bkyerv/culprit`, private; P0 commit `8a864e4` pushed to `main`. |
| Dedicated Google Cloud project | verified | `culprit-6f973`, project number `859405737127`, created 2026-08-23. |
| Billing, tag, and APIs | partial | Billing, required APIs, label, and `$20`/`$50` alerts verified; exact Resource Manager tag blocked because no organization/tag key is visible. |
| Firestore and Cloud Storage | verified | Native database and lifecycle-managed `gs://culprit-6f973-state`, both `us-central1`. |
| Service accounts and IAM | partial | Runtime grants are least-privilege and verified; an unexpected concurrent user Token Creator binding on runner is preserved in `BLOCKERS.md`. |
| Minimal runner deployed with sandbox launcher | verified | Revision `culprit-runner-00001-npc` exports both `sandboxLauncher: true` and gen2. |
| Sandbox CLI help captured | verified | Six help commands exited 0; raw output in `docs/p0-probe-report.json`, summary in `docs/sandbox-cli-reference.md`. |
| Sandbox A → GCS → sandbox B round-trip | verified | Different B restored the GCS-downloaded tar and read the exact 117 source bytes; all hashes match. |
| Successful report artifact saved | verified | `docs/p0-probe-report.json` records `all_checks_passed: true`; committed and pushed in `8a864e4`. |

**Gate P0 is green and was reconciled against the deployed project on 2026-08-23.** The project,
runner deployment, and cross-sandbox round-trip are verified facts, not pending work.

## P1 — Recording

| Deliverable | Status | Evidence |
|---|---|---|
| Domain model | verified | Pydantic models cover Run, Event, Checkpoint, Effect, Criterion, Grade, CapabilitySet, and every Event requires capabilities; local tests pass. |
| Stateful sandbox driver | verified | Live run `run-20260823T020807Z-b5034757` seeded via `exec`, executed tools, and exported sandbox world state through the P0-verified CLI forms. |
| Effect broker | verified | Both supplier emails are ledger entries in `simulate` mode with constrained `gemini-3.7-flash` world-model responses; record mode is hard-disabled and no external sender exists. |
| SubjectAgent and tool surface | verified | ADK SubjectAgent used `gemini-3.7-flash` on Vertex `global`; every one of 11 Firestore events has all seven requested tools in its capability snapshot, tokens, latency, and computed cost. |
| Firestore events and world-state checkpoints | verified | Independent query returned event seq `0..10` and effect seq `0..1`; all three GCS checkpoint byte counts and SHA-256 hashes match Firestore. |
| Hero scenario | verified | YAML loads as data, seed contains three quotes, XLSX internal model, and communications policy; generated XLSX archive validates. |
| Scenario CLI and trace dump | verified | CLI submitted through internal Cloud Tasks, waited for completion, and wrote `docs/p1-hero-trace.json`, byte-identical to the GCS artifact. |
| Local checks | verified | `uv lock --check`, Ruff, compile, tool-schema inspection, and 9 pytest tests pass. |

**Gate P1 is green.** Real run `run-20260823T020807Z-b5034757` completed with two brokered
supplier emails, ordered queryable events, matching GCS hashes, and sandbox egress denied. Both
emails leaked the internal `27.5%` margin value as a market benchmark, which is the expected failure
input for P3. No email or HTTP request was actually sent.
