#!/usr/bin/env bash
# Fallback when local gcloud cannot transmit sandboxLauncher (too old for
# --sandbox-launcher / services replace). Build, then PUT the service spec
# with the new digest via the Cloud Run Admin API.
set -Eeuo pipefail

PROJECT_ID="${CULPRIT_PROJECT_ID:-culprit-6f973}"
REGION="${CULPRIT_REGION:-us-central1}"
ARTIFACT_REPOSITORY="${CULPRIT_ARTIFACT_REPOSITORY:-culprit}"
SERVICE_NAME="culprit-runner"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
EXPECTED_ACCOUNT="${CULPRIT_OPERATOR_ACCOUNT:?set CULPRIT_OPERATOR_ACCOUNT to the gcloud account allowed to deploy}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/runner:p3-investigation"
READY_TIMEOUT_SECONDS=300

if [[ ! "${PROJECT_ID}" =~ ^culprit-[a-z0-9]{5}$ ]]; then
  echo "Refusing unexpected project id: ${PROJECT_ID}" >&2
  exit 2
fi

active_account="$("${GCLOUD_BIN}" auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
if [[ "${active_account}" != "${EXPECTED_ACCOUNT}" ]]; then
  echo "Refusing to deploy as unexpected gcloud account: ${active_account:-none}" >&2
  echo "Expected CULPRIT_OPERATOR_ACCOUNT=${EXPECTED_ACCOUNT}" >&2
  exit 2
fi

PROJECT_NUMBER="${CULPRIT_PROJECT_NUMBER:-$("${GCLOUD_BIN}" projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
if [[ ! "${PROJECT_NUMBER}" =~ ^[0-9]+$ ]]; then
  echo "Refusing unexpected project number: ${PROJECT_NUMBER}" >&2
  exit 2
fi

PREV_REVISION="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.latestReadyRevisionName)')"
if [[ -z "${PREV_REVISION}" ]]; then
  echo "Could not resolve current latestReadyRevisionName for ${SERVICE_NAME}" >&2
  exit 1
fi

"${GCLOUD_BIN}" builds submit "${REPO_ROOT}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --config="${REPO_ROOT}/infra/cloudbuild-runner.yaml" \
  --substitutions="_IMAGE=${IMAGE}" \
  --quiet

DIGEST="$("${GCLOUD_BIN}" artifacts docker images describe "${IMAGE}" \
  --format='value(image_summary.digest)')"
if [[ -z "${DIGEST}" || "${DIGEST}" != sha256:* ]]; then
  echo "Failed to resolve image digest for ${IMAGE}" >&2
  exit 1
fi
IMAGE_DIGEST="${IMAGE%:*}@${DIGEST}"

EXPORT_YAML="$(mktemp)"
trap 'rm -f "${EXPORT_YAML}"' EXIT

"${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format=export >"${EXPORT_YAML}"

CULPRIT_FALLBACK_EXPORT="${EXPORT_YAML}" \
CULPRIT_FALLBACK_IMAGE="${IMAGE_DIGEST}" \
CULPRIT_PROJECT_NUMBER="${PROJECT_NUMBER}" \
CULPRIT_REGION="${REGION}" \
CULPRIT_SERVICE_NAME="${SERVICE_NAME}" \
  uv run --directory "${REPO_ROOT}" python <<'PY'
import os
import sys

import google.auth
import yaml
from google.auth.transport.requests import AuthorizedSession

export_path = os.environ["CULPRIT_FALLBACK_EXPORT"]
image = os.environ["CULPRIT_FALLBACK_IMAGE"]
project_number = os.environ["CULPRIT_PROJECT_NUMBER"]
region = os.environ["CULPRIT_REGION"]
service = os.environ["CULPRIT_SERVICE_NAME"]

with open(export_path, encoding="utf-8") as fh:
    spec = yaml.safe_load(fh)

annotations = spec.setdefault("metadata", {}).setdefault("annotations", {})
annotations.pop("run.googleapis.com/ingress-status", None)
annotations.pop("run.googleapis.com/urls", None)

containers = spec["spec"]["template"]["spec"]["containers"]
containers[0]["image"] = image

credentials, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
session = AuthorizedSession(credentials)
url = (
    f"https://{region}-run.googleapis.com/apis/serving.knative.dev/v1/"
    f"namespaces/{project_number}/services/{service}"
)
response = session.put(url, json=spec)
if response.status_code != 200:
    sys.stderr.write(
        f"PUT failed: HTTP {response.status_code}: {response.text}\n"
    )
    sys.exit(1)
print(f"PUT accepted: HTTP {response.status_code}")
PY

deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
NEW_REVISION=""
while (( SECONDS < deadline )); do
  candidate="$("${GCLOUD_BIN}" run revisions list \
    --service="${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --sort-by=~metadata.creationTimestamp \
    --limit=1 \
    --format='value(metadata.name)')"
  if [[ -n "${candidate}" && "${candidate}" != "${PREV_REVISION}" ]]; then
    ready="$("${GCLOUD_BIN}" run revisions describe "${candidate}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --format=json | uv run --directory "${REPO_ROOT}" python -c \
      'import json,sys; d=json.load(sys.stdin); print("True" if any(c.get("type")=="Ready" and c.get("status")=="True" for c in d.get("status",{}).get("conditions") or []) else "False")')"
    if [[ "${ready}" == "True" ]]; then
      latest_ready="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --format='value(status.latestReadyRevisionName)')"
      traffic_rev="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --format='value(status.traffic[0].revisionName)')"
      traffic_pct="$("${GCLOUD_BIN}" run services describe "${SERVICE_NAME}" \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --format='value(status.traffic[0].percent)')"
      if [[ "${latest_ready}" == "${candidate}" && "${traffic_rev}" == "${candidate}" && "${traffic_pct}" == "100" ]]; then
        NEW_REVISION="${candidate}"
        break
      fi
    fi
  fi
  sleep 5
done

if [[ -z "${NEW_REVISION}" ]]; then
  echo "Timed out after ${READY_TIMEOUT_SECONDS}s waiting for a Ready revision newer than ${PREV_REVISION}" >&2
  exit 1
fi

CREATED="$("${GCLOUD_BIN}" run revisions describe "${NEW_REVISION}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(metadata.creationTimestamp)')"
SERVED_IMAGE="$("${GCLOUD_BIN}" run revisions describe "${NEW_REVISION}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(spec.containers[0].image)')"

echo "revision: ${NEW_REVISION}"
echo "created: ${CREATED}"
echo "image: ${SERVED_IMAGE}"
