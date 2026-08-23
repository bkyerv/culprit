"""The domain-general SubjectAgent tool surface."""

from __future__ import annotations

import base64
import fnmatch
import json
import posixpath
from dataclasses import dataclass
from typing import Any

from culprit_core.models import CapabilitySet

from culprit_runner.effect_broker import EffectBroker
from culprit_runner.sandbox_driver import SandboxDriver

READ_TEXT_SCRIPT = r"""
import pathlib, sys
p = pathlib.Path('/work') / sys.argv[1]
data = p.read_bytes()
if len(data) > 240000:
    data = data[:240000]
    suffix = '\n[truncated at 240000 bytes]'
else:
    suffix = ''
sys.stdout.write(data.decode('utf-8', errors='replace') + suffix)
"""

READ_XLSX_SCRIPT = r"""
import json, pathlib, re, sys, zipfile
from xml.etree import ElementTree as ET
p = pathlib.Path('/work') / sys.argv[1]
ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}
with zipfile.ZipFile(p) as z:
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        shared = [''.join(t.text or '' for t in si.findall('.//m:t', ns))
                  for si in root.findall('m:si', ns)]
    workbook = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    targets = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels}
    result = {}
    for sheet in workbook.findall('m:sheets/m:sheet', ns):
        target = targets[sheet.attrib['{%s}id' % ns['r']]].lstrip('/')
        if not target.startswith('xl/'):
            target = 'xl/' + target
        root = ET.fromstring(z.read(target))
        rows = []
        for row in root.findall('.//m:sheetData/m:row', ns):
            values = {}
            for cell in row.findall('m:c', ns):
                ref = cell.attrib.get('r', '')
                value = cell.find('m:v', ns)
                if cell.attrib.get('t') == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.findall('.//m:t', ns))
                elif value is None:
                    text = ''
                elif cell.attrib.get('t') == 's':
                    text = shared[int(value.text)]
                else:
                    text = value.text
                values[ref] = text
            rows.append(values)
        result[sheet.attrib['name']] = rows
print(json.dumps(result, indent=2, sort_keys=True))
"""

LIST_DIR_SCRIPT = r"""
import json, pathlib, sys
p = pathlib.Path('/work') / sys.argv[1]
items = [{'name': x.name, 'path': str(x.relative_to('/work')), 'type': 'directory' if x.is_dir() else 'file',
          'bytes': x.stat().st_size if x.is_file() else None} for x in sorted(p.iterdir())]
print(json.dumps(items, sort_keys=True))
"""

WRITE_FILE_SCRIPT = r"""
import base64, pathlib, sys
p = pathlib.Path('/work') / sys.argv[1]
p.parent.mkdir(parents=True, exist_ok=True)
data = base64.b64decode(sys.argv[2])
p.write_bytes(data)
print(len(data))
"""


class CapabilityDenied(PermissionError):
    pass


def _normalise_path(path: str) -> str:
    cleaned = posixpath.normpath(path.strip().replace("\\", "/"))
    if cleaned in ("", "."):
        return ""
    if cleaned == ".." or cleaned.startswith(("/", "../")):
        raise CapabilityDenied(f"path escapes workspace: {path}")
    return cleaned


def _matches(path: str, patterns: list[str]) -> bool:
    if path == "":
        return bool(patterns)
    return any(
        pattern in {"*", "**", "**/*"}
        or fnmatch.fnmatchcase(path, pattern)
        or (pattern.endswith("/**") and path == pattern[:-3])
        for pattern in patterns
    )


@dataclass
class ToolSurface:
    functions: list[Any]
    mutating_tools: set[str]


def create_tool_surface(
    *,
    driver: SandboxDriver,
    sandbox_name: str,
    capabilities: CapabilitySet,
    broker: EffectBroker,
    user_answers: dict[str, str],
) -> ToolSurface:
    def require_tool(name: str) -> None:
        if name not in capabilities.allowed_tools:
            raise CapabilityDenied(f"tool is not allowed: {name}")

    def require_read(path: str) -> str:
        require_tool("read_file")
        normalised = _normalise_path(path)
        if not _matches(normalised, capabilities.readable_paths):
            raise CapabilityDenied(f"read is not allowed: {path}")
        return normalised

    def require_write(path: str) -> str:
        require_tool("write_file")
        normalised = _normalise_path(path)
        if not _matches(normalised, capabilities.writable_paths):
            raise CapabilityDenied(f"write is not allowed: {path}")
        return normalised

    def read_file(path: str) -> dict[str, Any]:
        """Read a UTF-8 text file or inspect an XLSX workbook in the isolated workspace."""

        try:
            safe_path = require_read(path)
            script = READ_XLSX_SCRIPT if safe_path.lower().endswith(".xlsx") else READ_TEXT_SCRIPT
            result = driver.exec(
                sandbox_name,
                ["/usr/local/bin/python", "-c", script, safe_path],
            )
            return {
                "ok": True,
                "path": safe_path,
                "format": "xlsx-json" if script == READ_XLSX_SCRIPT else "text",
                "content": result.stdout_text,
            }
        except Exception as exc:  # noqa: BLE001 - tool errors are observations for the agent.
            return {"ok": False, "error": str(exc)}

    def write_file(path: str, content: str) -> dict[str, Any]:
        """Write UTF-8 content to an allowed path in the isolated workspace."""

        try:
            safe_path = require_write(path)
            encoded = base64.b64encode(content.encode()).decode()
            result = driver.exec(
                sandbox_name,
                ["/usr/local/bin/python", "-c", WRITE_FILE_SCRIPT, safe_path, encoded],
            )
            return {"ok": True, "path": safe_path, "bytes": int(result.stdout_text.strip())}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def list_dir(path: str = ".") -> dict[str, Any]:
        """List files and directories at an allowed workspace path."""

        try:
            require_tool("list_dir")
            safe_path = _normalise_path(path)
            if not _matches(safe_path, capabilities.readable_paths):
                raise CapabilityDenied(f"directory listing is not allowed: {path}")
            result = driver.exec(
                sandbox_name,
                ["/usr/local/bin/python", "-c", LIST_DIR_SCRIPT, safe_path],
            )
            return {"ok": True, "path": safe_path or ".", "entries": json.loads(result.stdout)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def run_command(command: str) -> dict[str, Any]:
        """Run a shell command in /work inside the isolated, no-egress sandbox."""

        try:
            require_tool("run_command")
            result = driver.run_command(sandbox_name, command)
            return {
                "ok": result.exit_code == 0,
                "exit_code": result.exit_code,
                "stdout": result.stdout_text,
                "stderr": result.stderr_text,
                "timed_out": result.timed_out,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
        """Attempt an email through the simulated effect broker; no email is really sent."""

        try:
            require_tool("send_email")
            if "send_email" not in capabilities.effect_permissions:
                raise CapabilityDenied("send_email effect permission is not allowed")
            return await broker.perform("send_email", {"to": to, "subject": subject, "body": body})
        except Exception as exc:  # noqa: BLE001
            return {"simulated": True, "error": str(exc)}

    async def http_request(
        method: str,
        url: str,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Attempt HTTP through the simulated effect broker; never contact the requested URL."""

        try:
            require_tool("http_request")
            if "http_request" not in capabilities.effect_permissions:
                raise CapabilityDenied("http_request effect permission is not allowed")
            return await broker.perform(
                "http_request",
                {"method": method, "url": url, "body": body, "headers": headers or {}},
            )
        except Exception as exc:  # noqa: BLE001
            return {"simulated": True, "error": str(exc)}

    def ask_user(question: str) -> dict[str, Any]:
        """Ask for guidance at a recorded fork point using scenario-provided answers."""

        require_tool("ask_user")
        answer = user_answers.get(
            question, "No additional guidance is available; use your judgment."
        )
        return {"question": question, "answer": answer, "source": "scenario"}

    return ToolSurface(
        functions=[
            read_file,
            write_file,
            list_dir,
            run_command,
            send_email,
            http_request,
            ask_user,
        ],
        mutating_tools={"write_file", "run_command", "send_email", "http_request"},
    )
