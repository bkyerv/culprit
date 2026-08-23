"""Stateful driver for the verified Cloud Run sandbox CLI surface."""

from __future__ import annotations

import base64
import os
import posixpath
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SANDBOX_BINARY = Path("/usr/local/gcp/bin/sandbox")
COMMAND_TIMEOUT_SECONDS = 120
OUTPUT_LIMIT_BYTES = 256 * 1024


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    duration_ms: float
    timed_out: bool = False

    @property
    def stdout_text(self) -> str:
        return self.stdout[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="replace")

    def as_dict(self) -> dict[str, object]:
        return {
            "argv": self.argv,
            "exit_code": self.exit_code,
            "stdout": self.stdout_text,
            "stderr": self.stderr_text,
            "stdout_truncated": len(self.stdout) > OUTPUT_LIMIT_BYTES,
            "stderr_truncated": len(self.stderr) > OUTPUT_LIMIT_BYTES,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


class SandboxCommandError(RuntimeError):
    def __init__(self, stage: str, result: CommandResult) -> None:
        detail = result.stderr_text or result.stdout_text or "no command output"
        super().__init__(f"{stage} failed: {detail}")
        self.stage = stage
        self.result = result


class SandboxDriver:
    """Invoke only the P0-verified `run`, `exec`, `tar`, and `delete` forms."""

    def __init__(
        self,
        *,
        binary: Path = SANDBOX_BINARY,
        command_timeout: int = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if command_timeout > COMMAND_TIMEOUT_SECONDS:
            raise ValueError(
                f"sandbox command timeout cannot exceed {COMMAND_TIMEOUT_SECONDS} seconds"
            )
        self.binary = binary
        self.command_timeout = command_timeout

    def available(self) -> bool:
        return self.binary.is_file() and os.access(self.binary, os.X_OK)

    def _run(self, argv: list[str], *, timeout: int | None = None) -> CommandResult:
        if timeout is not None and timeout > self.command_timeout:
            raise ValueError(
                f"sandbox command timeout cannot exceed {self.command_timeout} seconds"
            )
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                timeout=timeout or self.command_timeout,
            )
            return CommandResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            if isinstance(stdout, str):
                stdout = stdout.encode()
            if isinstance(stderr, str):
                stderr = stderr.encode()
            return CommandResult(
                argv=argv,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                timed_out=True,
            )

    @staticmethod
    def _require(stage: str, result: CommandResult) -> CommandResult:
        if result.exit_code != 0:
            raise SandboxCommandError(stage, result)
        return result

    def start(self, name: str, *, import_tar: Path | None = None, lifetime: int = 900) -> None:
        argv = [str(self.binary), "run", name, "--detach", "--write"]
        if import_tar is not None:
            argv.append(f"--import-tar={import_tar}")
        argv.extend(["--", "/bin/sleep", str(lifetime)])
        self._require("sandbox run", self._run(argv))

    def exec(
        self,
        name: str,
        argv: list[str],
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        command = [str(self.binary), "exec", name, "--", *argv]
        result = self._run(command, timeout=timeout)
        return self._require("sandbox exec", result) if check else result

    def run_command(self, name: str, command: str, *, timeout: int | None = None) -> CommandResult:
        return self.exec(
            name,
            ["/bin/bash", "-c", f"cd /work && {command}"],
            timeout=timeout,
            check=False,
        )

    def write_bytes(self, name: str, relative_path: str, payload: bytes) -> CommandResult:
        """Seed one workspace file through the verified `sandbox exec` boundary."""

        cleaned = posixpath.normpath(relative_path.replace("\\", "/"))
        if cleaned in ("", "..") or cleaned.startswith(("/", "../")):
            raise ValueError(f"invalid workspace seed path: {relative_path}")
        script = (
            "import base64,pathlib,sys; "
            "p=pathlib.Path('/work')/sys.argv[1]; "
            "p.parent.mkdir(parents=True,exist_ok=True); "
            "p.write_bytes(base64.b64decode(sys.argv[2]))"
        )
        return self.exec(
            name,
            [
                "/usr/local/bin/python",
                "-c",
                script,
                cleaned,
                base64.b64encode(payload).decode(),
            ],
        )

    def export_tar(self, name: str, destination: Path) -> CommandResult:
        result = self._run([str(self.binary), "tar", name, f"--file={destination}"])
        self._require("sandbox tar", result)
        if not destination.is_file():
            raise RuntimeError(f"sandbox tar succeeded but did not create {destination}")
        return result

    def delete(self, name: str) -> list[CommandResult]:
        """Delete safely around the verified launcher cleanup hang."""

        attempts = [self._run([str(self.binary), "delete", name, "--force"], timeout=8)]
        if attempts[0].exit_code != 0:
            attempts.append(self._run([str(self.binary), "delete", name], timeout=8))
        return attempts
