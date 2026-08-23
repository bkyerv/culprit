from __future__ import annotations

import os

import requests
from google.auth.transport.requests import Request
from google.cloud import storage
from google.oauth2 import id_token


def main() -> None:
    service_url = os.environ["CULPRIT_PROBE_SERVICE_URL"].rstrip("/")
    bucket_name = os.environ["CULPRIT_BUCKET"]
    report_object = os.environ["CULPRIT_PROBE_REPORT_OBJECT"]

    token = id_token.fetch_id_token(Request(), service_url)
    response = requests.post(
        f"{service_url}/probe",
        headers={"Authorization": f"Bearer {token}"},
        timeout=840,
    )

    blob = storage.Client().bucket(bucket_name).blob(report_object)
    blob.upload_from_string(response.content, content_type="application/json")
    print(f"status={response.status_code}")
    print(f"report=gs://{bucket_name}/{report_object}")
    response.raise_for_status()


if __name__ == "__main__":
    main()
