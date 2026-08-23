from __future__ import annotations

from pathlib import Path

import pytest
from culprit_runner.sandbox_driver import CommandResult, SandboxDriver


class CapturingDriver(SandboxDriver):
    def __init__(self) -> None:
        super().__init__(binary=Path("/usr/local/gcp/bin/sandbox"))
        self.commands: list[list[str]] = []

    def _run(self, argv: list[str], *, timeout: int | None = None) -> CommandResult:
        self.commands.append(argv)
        return CommandResult(argv, 0, b"", b"", 1)


def test_start_uses_the_p0_verified_import_form() -> None:
    driver = CapturingDriver()
    driver.start("subject-1", import_tar=Path("/tmp/seed.tar"), lifetime=900)

    assert driver.commands == [
        [
            "/usr/local/gcp/bin/sandbox",
            "run",
            "subject-1",
            "--detach",
            "--write",
            "--import-tar=/tmp/seed.tar",
            "--",
            "/bin/sleep",
            "900",
        ]
    ]


def test_workspace_command_is_routed_through_sandbox_exec() -> None:
    driver = CapturingDriver()
    driver.run_command("subject-1", "python report.py")

    assert driver.commands == [
        [
            "/usr/local/gcp/bin/sandbox",
            "exec",
            "subject-1",
            "--",
            "/bin/bash",
            "-c",
            "cd /work && python report.py",
        ]
    ]


def test_sandbox_command_timeout_cannot_exceed_the_hard_limit() -> None:
    with pytest.raises(ValueError, match="cannot exceed 120 seconds"):
        SandboxDriver(command_timeout=121)
