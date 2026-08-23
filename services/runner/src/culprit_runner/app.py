from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from google.cloud import storage

SANDBOX_BINARY = Path("/usr/local/gcp/bin/sandbox")
COMMAND_TIMEOUT_SECONDS = 120
OUTPUT_LIMIT_BYTES = 256 * 1024

app = FastAPI(title="Culprit P0 sandbox probe", version="0.1.0")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> tuple[str, bool]:
    truncated = len(data) > OUTPUT_LIMIT_BYTES
    return data[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="replace"), truncated


def _run(argv: list[str], *, timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[dict[str, Any], bytes]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        stdout_text, stdout_truncated = _decode(stdout)
        stderr_text, stderr_truncated = _decode(stderr)
        return (
            {
                "argv": argv,
                "exit_code": completed.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            stdout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if isinstance(stderr, str):
            stderr = stderr.encode()
        stdout_text, stdout_truncated = _decode(stdout)
        stderr_text, stderr_truncated = _decode(stderr)
        return (
            {
                "argv": argv,
                "exit_code": None,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "timed_out": True,
                "timeout_seconds": timeout,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            stdout,
        )
    except OSError as exc:
        return (
            {
                "argv": argv,
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc),
                "stdout_truncated": False,
                "stderr_truncated": False,
                "error_type": type(exc).__name__,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            b"",
        )


def _require_success(stage: str, result: dict[str, Any]) -> None:
    if result.get("exit_code") != 0:
        detail = result.get("stderr") or result.get("stdout") or "no command output"
        raise RuntimeError(f"{stage} failed: {detail}")


def _delete_sandbox(name: str) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    force_result, _ = _run([str(SANDBOX_BINARY), "delete", name, "--force"])
    attempts.append(force_result)
    if force_result.get("exit_code") != 0:
        plain_result, _ = _run([str(SANDBOX_BINARY), "delete", name])
        attempts.append(plain_result)
    return attempts


def _probe() -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    sandbox_a = f"p0-a-{request_id[:12]}"
    sandbox_b = f"p0-b-{request_id[:12]}"
    bucket_name = os.environ.get("CULPRIT_BUCKET", "")
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    source_content = (
        "Culprit P0 sandbox checkpoint probe\n"
        f"request_id={request_id}\n"
        "known_content=byte-identical-or-fail\n"
    ).encode()

    report: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "started_at": _utc_now(),
        "success": False,
        "environment": {
            "project_id": project_id,
            "bucket": bucket_name,
            "service": os.environ.get("K_SERVICE"),
            "revision": os.environ.get("K_REVISION"),
            "configuration": os.environ.get("K_CONFIGURATION"),
            "sandbox_binary": str(SANDBOX_BINARY),
            "sandbox_binary_exists": SANDBOX_BINARY.exists(),
            "sandbox_binary_executable": os.access(SANDBOX_BINARY, os.X_OK),
        },
        "help": {},
        "round_trip": {
            "sandbox_a": sandbox_a,
            "sandbox_b": sandbox_b,
            "different_sandbox_ids": sandbox_a != sandbox_b,
            "source": {
                "bytes": len(source_content),
                "sha256": _sha256(source_content),
                "utf8": source_content.decode(),
            },
            "commands": {},
            "gcs": {},
            "verification": {},
        },
        "cleanup": {},
    }

    help_commands: dict[str, list[str]] = {
        "sandbox": [str(SANDBOX_BINARY), "--help"],
        "sandbox run": [str(SANDBOX_BINARY), "run", "--help"],
        "sandbox exec": [str(SANDBOX_BINARY), "exec", "--help"],
        "sandbox tar": [str(SANDBOX_BINARY), "tar", "--help"],
        "sandbox do": [str(SANDBOX_BINARY), "do", "--help"],
        "sandbox delete": [str(SANDBOX_BINARY), "delete", "--help"],
    }
    for name, argv in help_commands.items():
        help_result, _ = _run(argv)
        report["help"][name] = help_result

    if not report["environment"]["sandbox_binary_executable"]:
        report["error"] = {
            "stage": "preflight",
            "type": "SandboxBinaryUnavailable",
            "message": f"{SANDBOX_BINARY} does not exist or is not executable",
        }
        report["completed_at"] = _utc_now()
        return report

    commands = report["round_trip"]["commands"]
    sandbox_a_started = False
    sandbox_b_started = False
    try:
        with tempfile.TemporaryDirectory(prefix="culprit-p0-") as temp_dir:
            temp_path = Path(temp_dir)
            exported_tar = temp_path / "sandbox-a-export.tar"
            downloaded_tar = temp_path / "sandbox-b-import.tar"

            run_a, _ = _run(
                [
                    str(SANDBOX_BINARY),
                    "run",
                    sandbox_a,
                    "--detach",
                    "--write",
                    "--",
                    "/bin/sleep",
                    "300",
                ]
            )
            commands["run_a"] = run_a
            _require_success("run_a", run_a)
            sandbox_a_started = True

            write_script = (
                "from pathlib import Path; "
                "path=Path('/work/probe.txt'); "
                "path.parent.mkdir(parents=True, exist_ok=True); "
                f"path.write_bytes(bytes.fromhex('{source_content.hex()}'))"
            )
            write_a, _ = _run(
                [
                    str(SANDBOX_BINARY),
                    "exec",
                    sandbox_a,
                    "--",
                    "/usr/local/bin/python",
                    "-c",
                    write_script,
                ]
            )
            commands["write_a"] = write_a
            _require_success("write_a", write_a)

            read_argv = [
                str(SANDBOX_BINARY),
                "exec",
                sandbox_a,
                "--",
                "/usr/local/bin/python",
                "-c",
                "import sys; sys.stdout.buffer.write(open('/work/probe.txt','rb').read())",
            ]
            read_a, content_a = _run(read_argv)
            commands["read_a"] = read_a
            _require_success("read_a", read_a)

            export_a, _ = _run(
                [str(SANDBOX_BINARY), "tar", sandbox_a, f"--file={exported_tar}"]
            )
            commands["export_a"] = export_a
            _require_success("export_a", export_a)
            if not exported_tar.is_file():
                raise RuntimeError(f"sandbox tar succeeded but did not create {exported_tar}")

            exported_tar_bytes = exported_tar.read_bytes()
            object_name = f"tmp/p0-probes/{request_id}/sandbox-a.tar"
            client = storage.Client(project=project_id or None)
            blob = client.bucket(bucket_name).blob(object_name)
            blob.upload_from_filename(exported_tar, content_type="application/x-tar")
            blob.reload()
            blob.download_to_filename(downloaded_tar)
            downloaded_tar_bytes = downloaded_tar.read_bytes()
            report["round_trip"]["gcs"] = {
                "uri": f"gs://{bucket_name}/{object_name}",
                "generation": str(blob.generation),
                "size": int(blob.size or 0),
                "crc32c": blob.crc32c,
                "exported_tar_bytes": len(exported_tar_bytes),
                "exported_tar_sha256": _sha256(exported_tar_bytes),
                "downloaded_tar_bytes": len(downloaded_tar_bytes),
                "downloaded_tar_sha256": _sha256(downloaded_tar_bytes),
                "upload_download_byte_identical": exported_tar_bytes == downloaded_tar_bytes,
                "export_path": str(exported_tar),
                "import_path": str(downloaded_tar),
                "distinct_local_paths": exported_tar != downloaded_tar,
            }

            run_b, _ = _run(
                [
                    str(SANDBOX_BINARY),
                    "run",
                    sandbox_b,
                    "--detach",
                    "--write",
                    f"--import-tar={downloaded_tar}",
                    "--",
                    "/bin/sleep",
                    "300",
                ]
            )
            commands["run_b"] = run_b
            _require_success("run_b", run_b)
            sandbox_b_started = True

            read_b, content_b = _run(
                [
                    str(SANDBOX_BINARY),
                    "exec",
                    sandbox_b,
                    "--",
                    "/usr/local/bin/python",
                    "-c",
                    "import sys; sys.stdout.buffer.write(open('/work/probe.txt','rb').read())",
                ]
            )
            commands["read_b"] = read_b
            _require_success("read_b", read_b)

            verification = {
                "sandbox_a_read_bytes": len(content_a),
                "sandbox_a_read_sha256": _sha256(content_a),
                "sandbox_a_matches_source": content_a == source_content,
                "sandbox_b_read_bytes": len(content_b),
                "sandbox_b_read_sha256": _sha256(content_b),
                "sandbox_b_matches_source": content_b == source_content,
                "sandbox_b_matches_sandbox_a": content_b == content_a,
                "all_checks_passed": all(
                    (
                        sandbox_a != sandbox_b,
                        content_a == source_content,
                        content_b == source_content,
                        content_b == content_a,
                        exported_tar_bytes == downloaded_tar_bytes,
                    )
                ),
            }
            report["round_trip"]["verification"] = verification
            report["success"] = verification["all_checks_passed"]
    except Exception as exc:  # noqa: BLE001 - the report must survive every failed experiment.
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        # A failed `run` can still leave a name allocated, so cleanup both IDs.
        report["cleanup"]["sandbox_a"] = _delete_sandbox(sandbox_a)
        report["cleanup"]["sandbox_b"] = _delete_sandbox(sandbox_b)
        report["cleanup"]["sandbox_a_was_started"] = sandbox_a_started
        report["cleanup"]["sandbox_b_was_started"] = sandbox_b_started

    report["completed_at"] = _utc_now()
    return report


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/probe", methods=["GET", "POST"])
def probe() -> dict[str, Any]:
    return _probe()
