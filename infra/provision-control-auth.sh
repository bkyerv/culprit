#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
SECRET_NAME="culprit-basic-auth"
CONTROL_SERVICE_ACCOUNT="culprit-control@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCLOUD_BIN="${GCLOUD_BIN:-${REPO_ROOT}/.deploy/google-cloud-sdk/bin/gcloud}"

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

active_account="$("${GCLOUD_BIN}" auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
if [[ "${active_account}" != "bkyerv@gmail.com" ]]; then
  echo "Refusing to provision as unexpected gcloud account: ${active_account:-none}" >&2
  exit 2
fi

if ! "${GCLOUD_BIN}" secrets describe "${SECRET_NAME}" \
  --project="${PROJECT_ID}" --format='value(name)' >/dev/null 2>&1; then
  auth_username="culprit-$(openssl rand -hex 4)"
  auth_password="$(openssl rand -base64 36 | tr -d '\n')"
  printf '%s:%s' "${auth_username}" "${auth_password}" | \
    "${GCLOUD_BIN}" secrets create "${SECRET_NAME}" \
      --project="${PROJECT_ID}" \
      --replication-policy=automatic \
      --data-file=- \
      --quiet
fi

"${GCLOUD_BIN}" secrets add-iam-policy-binding "${SECRET_NAME}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${CONTROL_SERVICE_ACCOUNT}" \
  --role=roles/secretmanager.secretAccessor \
  --condition=None \
  --quiet >/dev/null

echo "Control Basic Auth secret is ready in Secret Manager: ${SECRET_NAME}"
