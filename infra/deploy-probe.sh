#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
REGION="${CULPRIT_REGION:-us-central1}"
BUCKET_NAME="${CULPRIT_BUCKET_NAME:-${PROJECT_ID}-state}"
ARTIFACT_REPOSITORY="${CULPRIT_ARTIFACT_REPOSITORY:-culprit}"
SERVICE_NAME="culprit-runner"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/runner:p0-probe"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

if ! "${GCLOUD_BIN}" beta run deploy --help 2>&1 | grep -q -- '--sandbox-launcher'; then
  echo "This deployment requires a current gcloud beta with --sandbox-launcher." >&2
  exit 2
fi

"${GCLOUD_BIN}" builds submit "${REPO_ROOT}/services/runner" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --tag="${IMAGE}" \
  --quiet

"${GCLOUD_BIN}" beta run deploy "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${RUNNER_SERVICE_ACCOUNT}" \
  --execution-environment=gen2 \
  --sandbox-launcher \
  --concurrency=2 \
  --min-instances=0 \
  --max-instances=2 \
  --cpu=2 \
  --memory=4Gi \
  --timeout=900 \
  --ingress=internal \
  --no-allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},CULPRIT_BUCKET=${BUCKET_NAME}" \
  --quiet

"${GCLOUD_BIN}" run services add-iam-policy-binding "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member='user:bkyerv@gmail.com' \
  --role=roles/run.invoker \
  --quiet >/dev/null

"${GCLOUD_BIN}" run services add-iam-policy-binding "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${RUNNER_SERVICE_ACCOUNT}" \
  --role=roles/run.invoker \
  --quiet >/dev/null

"${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)'
