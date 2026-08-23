#!/usr/bin/env bash
set -Eeuo pipefail

# Idempotent P0 foundation provisioning for Culprit.
# Every mutating gcloud command is scoped to this dedicated project or to the
# explicitly approved billing account.

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
PROJECT_NAME="${CULPRIT_PROJECT_NAME:-Culprit}"
BILLING_ACCOUNT="${CULPRIT_BILLING_ACCOUNT:-01DD46-68941A-993ABB}"
REGION="${CULPRIT_REGION:-us-central1}"
BUCKET_NAME="${CULPRIT_BUCKET_NAME:-${PROJECT_ID}-state}"
ARTIFACT_REPOSITORY="${CULPRIT_ARTIFACT_REPOSITORY:-culprit}"
CONTROL_SERVICE_ACCOUNT="culprit-control@${PROJECT_ID}.iam.gserviceaccount.com"
RUNNER_SERVICE_ACCOUNT="culprit-runner@${PROJECT_ID}.iam.gserviceaccount.com"
BUDGET_DISPLAY_NAME="Culprit ${PROJECT_ID} monthly guardrail"
ENVIRONMENT_TAG="${CULPRIT_ENVIRONMENT_TAG:-}"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

retry() {
  local attempt=1
  local max_attempts=5
  local delay=4

  until "$@"; do
    if (( attempt >= max_attempts )); then
      echo "Command failed after ${attempt} attempts: $*" >&2
      return 1
    fi
    echo "Attempt ${attempt} failed; retrying in ${delay}s: $*" >&2
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

require_active_account() {
  local account
  account="$("${GCLOUD_BIN}" auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
  if [[ "${account}" != "bkyerv@gmail.com" ]]; then
    echo "Refusing to provision as unexpected gcloud account: ${account:-none}" >&2
    exit 2
  fi
}

project_exists() {
  "${GCLOUD_BIN}" projects describe "${PROJECT_ID}" --format='value(projectId)' >/dev/null 2>&1
}

service_account_exists() {
  "${GCLOUD_BIN}" iam service-accounts describe "$1" \
    --project="${PROJECT_ID}" --format='value(email)' >/dev/null 2>&1
}

bind_project_role() {
  local member="$1"
  local role="$2"
  retry "${GCLOUD_BIN}" projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="${member}" --role="${role}" --condition=None --quiet >/dev/null
}

require_active_account

if ! project_exists; then
  create_args=(
    projects create "${PROJECT_ID}"
    --name="${PROJECT_NAME}"
    --labels=environment=development
    --quiet
  )
  if [[ -n "${ENVIRONMENT_TAG}" ]]; then
    create_args+=(--tags="${ENVIRONMENT_TAG}")
  fi
  retry "${GCLOUD_BIN}" "${create_args[@]}"
fi

# Project labels are distinct from Resource Manager tags, but provide an
# in-project classification when no organization-scoped tag key is available.
retry "${GCLOUD_BIN}" alpha projects update "${PROJECT_ID}" \
  --update-labels=environment=development --quiet >/dev/null

project_number="$("${GCLOUD_BIN}" projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
if [[ -n "${ENVIRONMENT_TAG}" ]]; then
  tag_value_name="$("${GCLOUD_BIN}" resource-manager tags values describe \
    "${ENVIRONMENT_TAG}" --billing-project="${PROJECT_ID}" --format='value(name)')"
  tag_parent="//cloudresourcemanager.googleapis.com/projects/${project_number}"
  existing_tag_binding="$("${GCLOUD_BIN}" resource-manager tags bindings list \
    --parent="${tag_parent}" \
    --filter="tagValue=${tag_value_name}" \
    --format='value(name)' --limit=1)"
  if [[ -z "${existing_tag_binding}" ]]; then
    retry "${GCLOUD_BIN}" resource-manager tags bindings create \
      --parent="${tag_parent}" \
      --tag-value="${tag_value_name}" \
      --billing-project="${PROJECT_ID}" \
      --quiet
  fi
fi

billing_enabled="$("${GCLOUD_BIN}" billing projects describe "${PROJECT_ID}" \
  --format='value(billingEnabled)' 2>/dev/null || true)"
if [[ "${billing_enabled}" != "True" ]]; then
  retry "${GCLOUD_BIN}" billing projects link "${PROJECT_ID}" \
    --billing-account="${BILLING_ACCOUNT}" --quiet
fi

apis=(
  run.googleapis.com
  firestore.googleapis.com
  storage.googleapis.com
  cloudtasks.googleapis.com
  secretmanager.googleapis.com
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  aiplatform.googleapis.com
  iam.googleapis.com
  cloudresourcemanager.googleapis.com
  cloudbilling.googleapis.com
  billingbudgets.googleapis.com
)
retry "${GCLOUD_BIN}" services enable "${apis[@]}" --project="${PROJECT_ID}" --quiet

if ! "${GCLOUD_BIN}" firestore databases describe --database='(default)' \
  --project="${PROJECT_ID}" --format='value(name)' >/dev/null 2>&1; then
  retry "${GCLOUD_BIN}" firestore databases create --database='(default)' \
    --location="${REGION}" --type=firestore-native --project="${PROJECT_ID}" --quiet
fi

if ! "${GCLOUD_BIN}" storage buckets describe "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" --format='value(name)' >/dev/null 2>&1; then
  retry "${GCLOUD_BIN}" storage buckets create "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi
retry "${GCLOUD_BIN}" storage buckets update "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" --lifecycle-file="${SCRIPT_DIR}/bucket-lifecycle.json" >/dev/null

if ! "${GCLOUD_BIN}" artifacts repositories describe "${ARTIFACT_REPOSITORY}" \
  --project="${PROJECT_ID}" --location="${REGION}" --format='value(name)' >/dev/null 2>&1; then
  retry "${GCLOUD_BIN}" artifacts repositories create "${ARTIFACT_REPOSITORY}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --repository-format=docker \
    --description='Culprit service images' \
    --quiet
fi

if ! service_account_exists "${CONTROL_SERVICE_ACCOUNT}"; then
  retry "${GCLOUD_BIN}" iam service-accounts create culprit-control \
    --project="${PROJECT_ID}" \
    --display-name='Culprit control plane' \
    --description='Trusted Culprit control-plane runtime identity'
fi
if ! service_account_exists "${RUNNER_SERVICE_ACCOUNT}"; then
  retry "${GCLOUD_BIN}" iam service-accounts create culprit-runner \
    --project="${PROJECT_ID}" \
    --display-name='Culprit runner' \
    --description='Culprit sandbox runner runtime identity'
fi

bind_project_role "serviceAccount:${CONTROL_SERVICE_ACCOUNT}" roles/datastore.user
bind_project_role "serviceAccount:${CONTROL_SERVICE_ACCOUNT}" roles/cloudtasks.enqueuer
bind_project_role "serviceAccount:${RUNNER_SERVICE_ACCOUNT}" roles/datastore.user
bind_project_role "serviceAccount:${RUNNER_SERVICE_ACCOUNT}" roles/aiplatform.user

retry "${GCLOUD_BIN}" storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${CONTROL_SERVICE_ACCOUNT}" \
  --role=roles/storage.objectAdmin --quiet >/dev/null
retry "${GCLOUD_BIN}" storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${RUNNER_SERVICE_ACCOUNT}" \
  --role=roles/storage.objectAdmin --quiet >/dev/null

# Lets the control identity select only the runner identity for future Cloud
# Tasks OIDC tokens. It grants no project-wide service-account impersonation.
retry "${GCLOUD_BIN}" iam service-accounts add-iam-policy-binding \
  "${RUNNER_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${CONTROL_SERVICE_ACCOUNT}" \
  --role=roles/iam.serviceAccountUser --quiet >/dev/null

budget_name="$("${GCLOUD_BIN}" billing budgets list \
  --billing-account="${BILLING_ACCOUNT}" \
  --billing-project="${PROJECT_ID}" \
  --filter="displayName='${BUDGET_DISPLAY_NAME}'" \
  --format='value(name)' --limit=1 2>/dev/null || true)"
if [[ -z "${budget_name}" ]]; then
  retry "${GCLOUD_BIN}" billing budgets create \
    --billing-account="${BILLING_ACCOUNT}" \
    --billing-project="${PROJECT_ID}" \
    --display-name="${BUDGET_DISPLAY_NAME}" \
    --budget-amount=50USD \
    --filter-projects="projects/${PROJECT_ID}" \
    --calendar-period=month \
    --threshold-rule=percent=0.4,basis=current-spend \
    --threshold-rule=percent=1.0,basis=current-spend \
    --ownership-scope=billing-account \
    --quiet
fi

echo "Culprit foundation is ready."
echo "project_id=${PROJECT_ID}"
echo "project_number=${project_number}"
echo "bucket=gs://${BUCKET_NAME}"
echo "artifact_repository=${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}"
echo "control_service_account=${CONTROL_SERVICE_ACCOUNT}"
echo "runner_service_account=${RUNNER_SERVICE_ACCOUNT}"
