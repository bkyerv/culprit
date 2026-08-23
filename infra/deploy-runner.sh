#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
REGION="${CULPRIT_REGION:-us-central1}"
BUCKET_NAME="${CULPRIT_BUCKET_NAME:-${PROJECT_ID}-state}"
ARTIFACT_REPOSITORY="${CULPRIT_ARTIFACT_REPOSITORY:-culprit}"
SERVICE_NAME="culprit-runner"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCLOUD_BIN="${GCLOUD_BIN:-${REPO_ROOT}/.deploy/google-cloud-sdk/bin/gcloud}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/runner:p2-forking"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

active_account="$("${GCLOUD_BIN}" auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
if [[ "${active_account}" != "bkyerv@gmail.com" ]]; then
  echo "Refusing to deploy as unexpected gcloud account: ${active_account:-none}" >&2
  exit 2
fi

if ! "${GCLOUD_BIN}" beta run deploy --help 2>&1 | grep -q -- '--sandbox-launcher'; then
  echo "This deployment requires a current gcloud beta with --sandbox-launcher." >&2
  exit 2
fi

"${GCLOUD_BIN}" builds submit "${REPO_ROOT}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --config="${REPO_ROOT}/infra/cloudbuild-runner.yaml" \
  --substitutions="_IMAGE=${IMAGE}" \
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
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=TRUE,CULPRIT_BUCKET=${BUCKET_NAME}" \
  --quiet

"${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='yaml(status.url,status.latestReadyRevisionName,status.traffic,spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.containers[0].env)'
