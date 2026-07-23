"""Authenticated proxy to the co-located core's full HTTP surface.

P5 (docs/CLOUD_MIGRATION.md): the desktop Hub and the desktop MCP
server speak the CORE's protocol (plugin routes, /api/cmd), which the
public ingress does not expose (only the gateway's 8430 is public).
This proxy closes that gap: /core/<path> forwards verbatim to
http://localhost:8420/<path> for callers holding the service token.

Auth mirrors /ops/tick: constant-time compare against
CORTEX_SERVICE_TOKEN, accepted as EITHER a Bearer token or the
password of a Basic pair (the desktop clients authenticate to the Pi
with Basic auth today, so accepting Basic keeps their client code
unchanged: user stays 'cortex', password becomes the service token).
Deliberately not a gateway_tokens row: the desktop is the owner's own
machine, same trust class as the tick cron.
"""
from __future__ import annotations

import base64
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from ..config import get_settings

router = APIRouter(prefix="/core", tags=["core-proxy"])

_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)


def _presented_token(request: Request) -> str:
    authz = request.headers.get("authorization") or ""
    scheme, _, value = authz.partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    if scheme == "bearer":
        return value
    if scheme == "basic":
        try:
            decoded = base64.b64decode(value).decode("utf-8", "replace")
            _user, _, pwd = decoded.partition(":")
            return pwd
        except Exception:
            return ""
    return ""


def _check(request: Request) -> None:
    token = _presented_token(request)
    expected = get_settings().core_token
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "invalid core-proxy credentials")


@router.api_route("/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def core_proxy(path: str, request: Request):
    _check(request)
    s = get_settings()
    url = f"{s.core_url}/{path}"
    body = await request.body()
    # Forward the caller's headers so the core sees what it needs. The file
    # upload endpoint, for one, requires X-Filename / X-Description / X-Tags;
    # the old proxy sent only Content-Type, so every desktop import failed
    # with "Missing X-Filename header". Drop the hop-by-hop headers plus the
    # caller's auth/host (the proxy re-authenticates to the core below) and
    # content-length (httpx recomputes it for the forwarded body).
    _drop = {"host", "authorization", "content-length", "connection",
             "accept-encoding", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _drop}
    headers.setdefault("Content-Type", "application/json")
    auth = (s.core_username, s.core_token)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, auth=auth) as client:
            upstream = await client.request(
                request.method, url, params=dict(request.query_params),
                content=body if body else None, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"core unreachable: {type(e).__name__}")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"))
