#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
REGION="${CULPRIT_REGION:-us-central1}"
SERVICE_NAME="culprit-runner"
QUEUE_NAME="culprit-recordings"
RUN_ID="${1:-run-20260823T023743Z-49a8a6d6}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi
if [[ ! "${RUN_ID}" =~ ^run-[a-zA-Z0-9-]+$ ]]; then
  echo "Refusing unexpected run id: ${RUN_ID}" >&2
  exit 2
fi

service_url="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

"${GCLOUD_BIN}" tasks queues update "${QUEUE_NAME}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --max-concurrent-dispatches=3 \
  --max-dispatches-per-second=10 \
  --max-attempts=1 \
  --max-retry-duration=0s \
  --quiet >/dev/null

export UV_HTTP_TIMEOUT=120
GOOGLE_CLOUD_QUOTA_PROJECT="${PROJECT_ID}" uv run culprit \
  --service-url="${service_url}" \
  investigate "${RUN_ID}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --queue="${QUEUE_NAME}" \
  --invoker-service-account="${RUNNER_SERVICE_ACCOUNT}" \
  --output="${REPO_ROOT}/docs/p3-investigation-record.json"
