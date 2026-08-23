#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
REGION="${CULPRIT_REGION:-us-central1}"
BUCKET_NAME="${CULPRIT_BUCKET_NAME:-${PROJECT_ID}-state}"
ARTIFACT_REPOSITORY="${CULPRIT_ARTIFACT_REPOSITORY:-culprit}"
SERVICE_NAME="culprit-runner"
JOB_NAME="culprit-p0-probe-invoker"
REPORT_OBJECT="tmp/p0-probes/latest-report.json"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/runner:p0-probe"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

service_url="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

"${GCLOUD_BIN}" builds submit "${REPO_ROOT}/services/runner" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --tag="${IMAGE}" \
  --quiet

"${GCLOUD_BIN}" run jobs deploy "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${RUNNER_SERVICE_ACCOUNT}" \
  --command=/usr/local/bin/python \
  --args=-m,culprit_runner.invoke_probe \
  --set-env-vars="CULPRIT_PROBE_SERVICE_URL=${service_url},CULPRIT_BUCKET=${BUCKET_NAME},CULPRIT_PROBE_REPORT_OBJECT=${REPORT_OBJECT}" \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=900 \
  --cpu=1 \
  --memory=512Mi \
  --quiet

"${GCLOUD_BIN}" run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait

"${GCLOUD_BIN}" storage cp "gs://${BUCKET_NAME}/${REPORT_OBJECT}" \
  "${REPO_ROOT}/.deploy/p0-probe-report.raw.json" \
  --project="${PROJECT_ID}"

echo "${REPO_ROOT}/.deploy/p0-probe-report.raw.json"
