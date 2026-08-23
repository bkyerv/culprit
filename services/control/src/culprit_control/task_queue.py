from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from google.protobuf import duration_pb2, timestamp_pb2

from culprit_control.auth import BasicCredentials
from culprit_control.settings import Settings


class TaskQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = tasks_v2.CloudTasksClient()

    def _create(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        deadline_seconds: int,
        task_id: str | None = None,
        schedule_delay_seconds: int = 0,
        oidc_service_account: str | None = None,
        authorization: str | None = None,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        http_request = tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=url,
            headers=headers,
            body=json.dumps(payload, separators=(",", ":")).encode(),
        )
        if oidc_service_account:
            http_request.oidc_token = tasks_v2.OidcToken(
                service_account_email=oidc_service_account,
                audience=self.settings.runner_url,
            )
        task = tasks_v2.Task(
            http_request=http_request,
            dispatch_deadline=duration_pb2.Duration(seconds=deadline_seconds),
        )
        if task_id:
            task.name = self.client.task_path(
                self.settings.project_id,
                self.settings.region,
                self.settings.queue_name,
                task_id,
            )
        if schedule_delay_seconds:
            scheduled = timestamp_pb2.Timestamp()
            scheduled.FromDatetime(datetime.now(UTC) + timedelta(seconds=schedule_delay_seconds))
            task.schedule_time = scheduled
        try:
            created = self.client.create_task(parent=self.settings.queue_path, task=task)
            return created.name
        except AlreadyExists:
            if not task.name:
                raise
            return task.name

    def enqueue_runner(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        task_id: str,
    ) -> str:
        return self._create(
            url=f"{self.settings.runner_url}{path}",
            payload=payload,
            deadline_seconds=900,
            task_id=task_id,
            oidc_service_account=self.settings.runner_service_account,
        )

    def enqueue_advance(
        self,
        *,
        investigation_id: str,
        run_id: str,
        attempt: int,
        credentials: BasicCredentials,
        delay_seconds: int = 5,
    ) -> str:
        return self._create(
            url=(
                f"{self.settings.control_url}/api/internal/investigations/"
                f"{investigation_id}/advance"
            ),
            payload={"run_id": run_id, "attempt": attempt},
            deadline_seconds=60,
            task_id=None,
            schedule_delay_seconds=delay_seconds,
            authorization=credentials.authorization_header,
        )
