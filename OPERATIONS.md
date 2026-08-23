# Operations

This file records non-secret infrastructure identifiers and verified operational facts. Secrets must
live in Secret Manager and must never be written here.

## Accounts

- Google Cloud authenticated account: `bkyerv@gmail.com` (verified with `gcloud auth list`)
- GitHub authenticated account: `bkyerv` (verified with `gh auth status`)

## Google Cloud

- Project ID: `culprit-6f973`
- Project number: `859405737127`
- Organization/folder parent: none visible to the authenticated account
- Environment label: `environment=development` (verified)
- Environment Resource Manager tag: blocked; see `BLOCKERS.md`
- Billing account: `01DD46-68941A-993ABB` (linked and billing enabled)
- Budget: `billingAccounts/01DD46-68941A-993ABB/budgets/464711c0-87a6-4324-ac64-fe10ef77c0d2`
  (`$50` monthly; current-spend alerts at `$20` and `$50`)
- Region: `us-central1`
- Firestore database: `projects/culprit-6f973/databases/(default)` (Native, `us-central1`)
- Cloud Storage bucket: `gs://culprit-6f973-state` (`us-central1`, uniform access, public access
  prevention enforced; `tmp/` objects expire after 7 days)
- Cloud Build source bucket: `gs://culprit-6f973_cloudbuild` (service-managed)
- Artifact Registry repository: `us-central1-docker.pkg.dev/culprit-6f973/culprit`
- Runner service account: `culprit-runner@culprit-6f973.iam.gserviceaccount.com`
- Control service account: `culprit-control@culprit-6f973.iam.gserviceaccount.com`
- Runtime grants: Firestore user for both; bucket-scoped Storage Object Admin for both; Cloud Tasks
  enqueuer for control; Vertex AI user for runner; control has service-account user only on runner
- Unexpected operator binding: user has Service Account Token Creator on runner; preserved and
  documented in `BLOCKERS.md`
- Runner service URL: `https://culprit-runner-859405737127.us-central1.run.app`
- Runner canonical URL: `https://culprit-runner-icwvykyjyq-uc.a.run.app`
- Runner revision: `culprit-runner-00001-npc` (100% traffic; authenticated internal ingress)
- Runner deployed image digest: `sha256:90bd1da4c1f94da28d168390163153d84f37064d3745984e48c4f55d592757c9`
- P0 invoker job: `culprit-p0-probe-invoker`
- Successful invoker execution: `culprit-p0-probe-invoker-8f6jb`
- Cloud Tasks queue: not created in P0
- Basic Auth secret: not created in P0

## GitHub

- Repository: `https://github.com/bkyerv/culprit`
- Default branch: `main`
- Visibility: private (verified; required until submission)
- P0 completion commit: `8a864e4`

## P0 probe

- Endpoint: `POST https://culprit-runner-859405737127.us-central1.run.app/probe` (IAM protected)
- Deployment settings: gen2, sandbox launcher enabled, concurrency 2, CPU 2, memory 4 GiB,
  request timeout 900s
- Report: `docs/p0-probe-report.json`
- Report GCS object: `gs://culprit-6f973-state/tmp/p0-probes/latest-report.json`
- Evidence tar: `gs://culprit-6f973-state/tmp/p0-probes/ebf27e8f2c6e4749a6b2cfa249871b53/sandbox-a.tar`
- Probe request ID: `ebf27e8f2c6e4749a6b2cfa249871b53`
- P0 gate: passed; different sandbox B read byte-identical content restored from the GCS tar
