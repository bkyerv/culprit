#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
PROJECT_NUMBER="${CULPRIT_PROJECT_NUMBER:-859405737127}"
REGION="${CULPRIT_REGION:-us-central1}"
BUCKET_NAME="${CULPRIT_BUCKET_NAME:-${PROJECT_ID}-state}"
ARTIFACT_REPOSITORY="${CULPRIT_ARTIFACT_REPOSITORY:-culprit}"
SERVICE_NAME="culprit-control"
QUEUE_NAME="culprit-recordings"
CONTROL_SERVICE_ACCOUNT="culprit-control@${PROJECT_ID}.iam.gserviceaccount.com"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"
RUNNER_URL="https://culprit-runner-${PROJECT_NUMBER}.${REGION}.run.app"
CONTROL_URL="https://${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"
DEFAULT_RUN_ID="run-20260823T023743Z-49a8a6d6"
DEFAULT_INVESTIGATION_ID="inv-20260823T061029Z-e17623ce"
SECRET_VERSION="projects/${PROJECT_NUMBER}/secrets/culprit-basic-auth/versions/latest"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCLOUD_BIN="${GCLOUD_BIN:-${REPO_ROOT}/.deploy/google-cloud-sdk/bin/gcloud}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/control:p4-live-ui"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi
if [[ ! "${PROJECT_NUMBER}" =~ ^[0-9]+$ ]]; then
  echo "Refusing unexpected project number: ${PROJECT_NUMBER}" >&2
  exit 2
fi

active_account="$("${GCLOUD_BIN}" auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
if [[ "${active_account}" != "bkyerv@gmail.com" ]]; then
  echo "Refusing to deploy as unexpected gcloud account: ${active_account:-none}" >&2
  exit 2
fi
"${GCLOUD_BIN}" secrets describe culprit-basic-auth \
  --project="${PROJECT_ID}" --format='value(name)' >/dev/null

"${GCLOUD_BIN}" builds submit "${REPO_ROOT}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --config="${REPO_ROOT}/infra/cloudbuild-control.yaml" \
  --substitutions="_IMAGE=${IMAGE}" \
  --quiet

"${GCLOUD_BIN}" run deploy "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${CONTROL_SERVICE_ACCOUNT}" \
  --execution-environment=gen2 \
  --concurrency=40 \
  --min-instances=0 \
  --max-instances=3 \
  --cpu=1 \
  --memory=512Mi \
  --timeout=3600 \
  --ingress=all \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},CULPRIT_PROJECT_NUMBER=${PROJECT_NUMBER},CULPRIT_REGION=${REGION},CULPRIT_BUCKET=${BUCKET_NAME},CULPRIT_QUEUE=${QUEUE_NAME},CULPRIT_RUNNER_URL=${RUNNER_URL},CULPRIT_CONTROL_URL=${CONTROL_URL},CULPRIT_RUNNER_SERVICE_ACCOUNT=${RUNNER_SERVICE_ACCOUNT},CULPRIT_BASIC_AUTH_SECRET=${SECRET_VERSION},CULPRIT_DEFAULT_RUN_ID=${DEFAULT_RUN_ID},CULPRIT_DEFAULT_INVESTIGATION_ID=${DEFAULT_INVESTIGATION_ID}" \
  --quiet

"${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='yaml(status.url,status.latestReadyRevisionName,status.traffic,spec.template.spec.containerConcurrency,spec.template.spec.containers[0].resources,spec.template.metadata.annotations,spec.template.spec.containers[0].env)'
