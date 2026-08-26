#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
REGION="${CULPRIT_REGION:-us-central1}"
BUCKET_NAME="${CULPRIT_BUCKET_NAME:-${PROJECT_ID}-state}"
SCENARIO_ID="${CULPRIT_SCENARIO_ID:-supplier-counter-offer}"
SERVICE_NAME="culprit-runner"
QUEUE_NAME="culprit-recordings"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

service_url="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

if ! "${GCLOUD_BIN}" tasks queues describe "${QUEUE_NAME}" \
  --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" tasks queues create "${QUEUE_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --max-concurrent-dispatches=3 \
    --max-dispatches-per-second=10 \
    --quiet
fi
"${GCLOUD_BIN}" tasks queues update "${QUEUE_NAME}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --max-concurrent-dispatches=3 \
  --max-dispatches-per-second=10 \
  --max-attempts=1 \
  --max-retry-duration=0s \
  --quiet >/dev/null

GOOGLE_CLOUD_QUOTA_PROJECT="${PROJECT_ID}" uv run culprit-record \
  --service-url="${service_url}" \
  run "${SCENARIO_ID}" \
  --via-cloud-tasks \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --queue="${QUEUE_NAME}" \
  --bucket="${BUCKET_NAME}" \
  --invoker-service-account="${RUNNER_SERVICE_ACCOUNT}" \
  --output="${REPO_ROOT}/docs/p1-hero-trace.json"
