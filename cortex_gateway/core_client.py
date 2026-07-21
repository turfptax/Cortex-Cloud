"""HTTP client to the co-located cortex-core, the corpus's single writer.

In the ATTACH topology (docs/CLOUD_MIGRATION.md P2) the gateway can only
READ the corpus files (mode=ro); every corpus write goes through the core
over localhost HTTP with the shared CORTEX_SERVICE_TOKEN Basic pair. This
keeps exactly one process opening cortex.db/overseer.db read-write, which
is what makes per-file Litestream replication and WAL semantics coherent.

Design points:
  - Sync client (httpx, already in the dependency tree via mcp): callers
    are FastAPI threadpool endpoints that were doing sync DB writes here
    anyway, so no async plumbing changes.
  - Tight timeouts: a down core must degrade a write request quickly and
    loudly, never hang the request thread.
  - CoreWriteError carries enough for the caller to shape a clean HTTP
    error without leaking the token (never logs auth headers).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger("cortex_gateway.core")

_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)


class CoreWriteError(RuntimeError):
    """A routed corpus write failed (core down, auth rejected, or the
    core answered non-2xx / ok:false)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CoreClient:
    """Thin sync client for the core's HTTP surface (port 8420 style)."""

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.core_url
        self._auth = (s.core_username, s.core_token)

    def request(self, method: str, path: str,
                payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._base + path
        try:
            r = httpx.request(method, url, json=payload, auth=self._auth,
                              timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            log.warning("core %s %s unreachable: %s", method, path, e)
            raise CoreWriteError(f"core unreachable: {e}") from e
        if r.status_code == 401:
            raise CoreWriteError("core rejected service credentials", 401)
        if r.status_code >= 400:
            raise CoreWriteError(
                f"core {method} {path} -> {r.status_code}", r.status_code)
        try:
            body = r.json()
        except ValueError as e:
            raise CoreWriteError(f"core returned non-JSON: {e}") from e
        if isinstance(body, dict) and body.get("ok") is False:
            raise CoreWriteError(
                str(body.get("error") or "core reported failure"),
                r.status_code)
        return body if isinstance(body, dict) else {"result": body}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)


def core() -> CoreClient:
    """Per-call constructor: settings are cached, httpx pools per-request.
    Cheap, and avoids a module-global client binding stale settings in
    tests that monkeypatch env."""
    return CoreClient()
