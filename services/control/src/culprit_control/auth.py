from __future__ import annotations

import base64
import binascii
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass

from google.cloud import secretmanager
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class BasicCredentials:
    username: bytes
    password: bytes

    @property
    def authorization_header(self) -> str:
        encoded = base64.b64encode(self.username + b":" + self.password).decode("ascii")
        return f"Basic {encoded}"


class SecretManagerCredentialLoader:
    def __init__(self, secret_version: str) -> None:
        self.secret_version = secret_version
        self._credentials: BasicCredentials | None = None
        self._lock = threading.Lock()

    def __call__(self) -> BasicCredentials:
        if self._credentials is not None:
            return self._credentials
        with self._lock:
            if self._credentials is None:
                payload = (
                    secretmanager.SecretManagerServiceClient()
                    .access_secret_version(request={"name": self.secret_version})
                    .payload.data
                )
                username, separator, password = payload.partition(b":")
                if not separator or not username or not password or b":" in username:
                    raise RuntimeError(
                        "culprit-basic-auth must contain one non-empty username:password pair"
                    )
                self._credentials = BasicCredentials(username=username, password=password)
        return self._credentials


class BasicAuthMiddleware:
    """Protect every HTTP route, including static files, with constant-time checks."""

    def __init__(
        self,
        app: ASGIApp,
        credential_loader: Callable[[], BasicCredentials],
    ) -> None:
        self.app = app
        self.credential_loader = credential_loader

    @staticmethod
    def _parse(header: bytes | None) -> tuple[bytes, bytes] | None:
        if not header:
            return None
        scheme, separator, encoded = header.partition(b" ")
        if not separator or scheme.lower() != b"basic":
            return None
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
        username, separator, password = decoded.partition(b":")
        if not separator:
            return None
        return username, password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        supplied = self._parse(headers.get(b"authorization"))
        expected = self.credential_loader()
        supplied_username, supplied_password = supplied or (b"", b"")
        username_matches = secrets.compare_digest(supplied_username, expected.username)
        password_matches = secrets.compare_digest(supplied_password, expected.password)
        authenticated = bool(supplied and username_matches and password_matches)
        if not authenticated:
            response = Response(
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Culprit", charset="UTF-8"',
                    "Cache-Control": "no-store",
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
