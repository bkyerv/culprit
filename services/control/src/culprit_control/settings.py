from __future__ import annotations

import os
import re
from dataclasses import dataclass

PROJECT_PATTERN = re.compile(r"^culprit-[a-z0-9]{5}$")


@dataclass(frozen=True)
class Settings:
    project_id: str
    region: str
    bucket_name: str
    queue_name: str
    runner_url: str
    control_url: str
    runner_service_account: str
    auth_secret_version: str
    default_run_id: str
    default_investigation_id: str
    stream_poll_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> Settings:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not PROJECT_PATTERN.fullmatch(project_id):
            raise RuntimeError(f"refusing unexpected Google Cloud project: {project_id or 'unset'}")
        region = os.environ.get("CULPRIT_REGION", "us-central1")
        project_number = os.environ.get("CULPRIT_PROJECT_NUMBER", "")
        runner_url = os.environ.get("CULPRIT_RUNNER_URL", "").rstrip("/")
        control_url = os.environ.get("CULPRIT_CONTROL_URL", "").rstrip("/")
        if not runner_url.startswith("https://") or not control_url.startswith("https://"):
            raise RuntimeError("CULPRIT_RUNNER_URL and CULPRIT_CONTROL_URL must be HTTPS URLs")
        if not project_number.isdigit():
            raise RuntimeError("CULPRIT_PROJECT_NUMBER must contain only digits")
        return cls(
            project_id=project_id,
            region=region,
            bucket_name=os.environ.get("CULPRIT_BUCKET", f"{project_id}-state"),
            queue_name=os.environ.get("CULPRIT_QUEUE", "culprit-recordings"),
            runner_url=runner_url,
            control_url=control_url,
            runner_service_account=os.environ.get(
                "CULPRIT_RUNNER_SERVICE_ACCOUNT",
                f"culprit-runner@{project_id}.iam.gserviceaccount.com",
            ),
            auth_secret_version=os.environ.get(
                "CULPRIT_BASIC_AUTH_SECRET",
                f"projects/{project_number}/secrets/culprit-basic-auth/versions/latest",
            ),
            default_run_id=os.environ.get("CULPRIT_DEFAULT_RUN_ID", ""),
            default_investigation_id=os.environ.get("CULPRIT_DEFAULT_INVESTIGATION_ID", ""),
            stream_poll_seconds=max(
                0.5, min(5.0, float(os.environ.get("CULPRIT_STREAM_POLL_SECONDS", "1")))
            ),
        )

    @property
    def queue_path(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}/queues/{self.queue_name}"
