"""Cloud Hub /api facade (Phase A, docs/CLOUD_MIGRATION.md).

The desktop Hub's React SPA calls a local FastAPI backend at /api/*.
Phase A serves that same SPA from THIS gateway (see GATEWAY_STATIC_DIR
in config.py) and this module replaces the desktop backend: it exposes
the /api surface the SPA expects and forwards to the co-located core
with the service token injected server-side. The browser never holds a
corpus credential; its session is the ACA Easy Auth (Entra) cookie.

Auth model: every route requires the x-ms-client-principal header that
Azure's Easy Auth injects for logged-in users and STRIPS from inbound
requests, so its presence cannot be spoofed on the cloud deployment.
That guarantee only holds behind Easy Auth, which is why this router
(and the SPA mount) activate ONLY in web-UI mode (GATEWAY_STATIC_DIR
set); a Pi/tunnel gateway keeps its surface unchanged.

Contract fidelity: the desktop backend swallows core failures into
HTTP-200 {ok:false, error} bodies (the SPA's apiFetch only throws on
non-2xx), returns some CMD responses parsed and others raw, and maps
/api/overseer/X mechanically onto the core's /plugins/overseer/X. All
of that is replicated here; see tests/test_hub_api.py.
"""
from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import grants
from ..config import get_settings
from ..identity import owner_ok

router = APIRouter(prefix="/api", tags=["hub-api"])

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _same_site_origin(request: Request) -> bool:
    """CSRF guard for cookie-authorized writes. The Entra session is a
    cookie, so a cross-site page could otherwise drive a state-changing
    /api call in the owner's browser. Require the browser-set Origin (or
    Referer) to match this gateway's own public origin on write methods.
    Read methods are exempt (no state change, and the corpus is already
    owner-gated). Non-browser callers do not use /api (they hold bearer
    tokens on /v1 or /core), so failing closed on a missing Origin for
    writes is safe."""
    allowed = get_settings().public_url
    if not allowed:
        return True   # dev/test without a configured public origin
    allowed_host = urlparse(allowed).netloc
    origin = request.headers.get("origin")
    if origin:
        return urlparse(origin).netloc == allowed_host
    referer = request.headers.get("referer")
    if referer:
        return urlparse(referer).netloc == allowed_host
    return False


def _require_login(request: Request) -> None:
    """Gate every /api call on an approved-owner Entra session.

    Presence of x-ms-client-principal proves an authenticated session
    (Easy Auth strips the header from inbound requests); owner_ok then
    pins it to the owner's object id so a non-owner tenant account gets
    403 instead of full corpus access. 401 (not a 302) so the SPA's
    fetches fail visibly rather than chasing redirects into HTML."""
    principal = request.headers.get("x-ms-client-principal")
    if not principal:
        raise HTTPException(401, "sign in required")
    if not owner_ok(principal, get_settings().owner_oids):
        raise HTTPException(403, "not authorized for this corpus")
    if request.method in _WRITE_METHODS and not _same_site_origin(request):
        raise HTTPException(403, "cross-site request blocked")


router.dependencies.append(Depends(_require_login))

# Per-route read timeouts for known-long core operations, matching the
# desktop backend's per-call values (overseer.py). Everything else gets
# the default. A4 replaces the worst of these with async jobs; until
# then the ACA ingress timeout is the real ceiling for the browser.
_LONG_READS = {
    "backfill": 600.0,
    "chat": 180.0,
    "quick-chat": 180.0,
    "questions/route-existing": 600.0,
    "projects/summary/refresh-all": 120.0,
    "narrative/generate": 120.0,
    "temporal/generate": 120.0,
    "insight/scan-now": 60.0,
    "insight/distill-corrections": 60.0,
    "imports/from-path": 300.0,
    "tick-now": 600.0,
}
_DEFAULT_READ = 60.0

# Desktop-only overseer routes read the desktop's local disk, so the
# cloud gateway has nothing to serve. Return SHAPE-COMPATIBLE empty
# successes (not ok:false) so the SPA degrades to "nothing to do"
# instead of misreading a crafted error as a result: the desktop
# backend's scan/import always return ok:true with found/counts keys,
# and the SPA reads those without checking ok. A3 hides these panels in
# cloud mode; until then this keeps them honest and non-broken.
_DESKTOP_NOTE = ("Local imports run on the desktop ingester; "
                 "this Hub is the cloud corpus.")
_DESKTOP_ONLY = {
    "scan/claude-code": {
        "ok": True, "scanned_dir": "(cloud)", "total": 0,
        "already_imported_count": 0, "new_count": 0, "found": [],
        "note": _DESKTOP_NOTE},
    "import": {
        "ok": True, "source": "claude-code",
        "counts": {"requested": 0, "imported": 0, "skipped": 0, "failed": 0},
        "imported": [], "skipped": [], "failed": [], "note": _DESKTOP_NOTE},
}

# Chat-attachment upload (A2). Mirrors the desktop backend + SPA
# constants so the browser upload path behaves identically: same field
# name ("files"), limits, extension allowlist, kind classifier, and
# ChatAttachmentRef response shape. Files stream to the core's
# /files/uploads as raw octet-stream with the X-Filename protocol.
_UPLOAD_MAX_FILES = 10
_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
_UPLOAD_TIMEOUT = 60.0
_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json",
              ".yaml", ".yml", ".csv", ".log", ".html", ".css", ".sh",
              ".sql", ".toml", ".ini", ".env"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_PDF_EXTS = {".pdf"}
_ALLOWED_EXTS = _TEXT_EXTS | _IMAGE_EXTS | _PDF_EXTS


def _classify_kind(filename: str, mime: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in _IMAGE_EXTS or (mime or "").startswith("image/"):
        return "image"
    if ext in _PDF_EXTS or mime == "application/pdf":
        return "pdf"
    if ext in _TEXT_EXTS or (mime or "").startswith("text/"):
        return "text"
    return "other"


def _timeout(read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=read, write=30.0, pool=5.0)


async def _core_get_json(path: str, *, params: dict | None = None,
                         read: float = _DEFAULT_READ) -> dict:
    """GET a core endpoint, desktop-backend error contract."""
    s = get_settings()
    try:
        async with httpx.AsyncClient(
                timeout=_timeout(read),
                auth=(s.core_username, s.core_token)) as client:
            resp = await client.get(f"{s.core_url}{path}", params=params)
        return resp.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "core request timed out"}
    except Exception as e:
        return {"ok": False, "error": f"Cannot reach core: {type(e).__name__}"}


async def _send_command(command: str, payload: dict | None = None,
                        *, read: float = 30.0) -> dict:
    """POST the core's /api/cmd protocol wrapper. Returns the core's
    {ok, response:"RSP:...|ACK:...|ERR:..."} envelope, or the error dict."""
    s = get_settings()
    body: dict = {"command": command}
    if payload is not None:
        body["payload"] = payload
    try:
        async with httpx.AsyncClient(
                timeout=_timeout(read),
                auth=(s.core_username, s.core_token)) as client:
            resp = await client.post(f"{s.core_url}/api/cmd", json=body)
        return resp.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "core request timed out"}
    except Exception as e:
        return {"ok": False, "error": f"Cannot reach core: {type(e).__name__}"}


def _parse_command(command: str, raw: dict) -> dict:
    """The desktop backend's send_command_parsed: unwrap RSP:/ACK: JSON
    into {data}, else {data: None, error}."""
    response = raw.get("response", "")
    for prefix in (f"RSP:{command}:", f"ACK:{command}:"):
        if isinstance(response, str) and response.startswith(prefix):
            try:
                return {"data": json.loads(response[len(prefix):])}
            except (json.JSONDecodeError, ValueError):
                break
    return {"data": None,
            "error": raw.get("error", f"Unexpected response for {command}")}


# -- health surfaces ---------------------------------------------------

@router.get("/health")
async def health():
    s = get_settings()
    return {"status": "ok", "mode": "cloud",
            "core_url": s.core_url}


# -- /api/connections (owner-facing connector approval from the WEB) ---
# The phone app manages grants via /v1/connections (app-scope token). A
# friend deploying their own instance may have no phone app, so mirror the
# same grant management here on the owner's Entra-authenticated /api surface
# (login + CSRF enforced by the router dependency). Same grants.* logic.


class _ApproveConnIn(BaseModel):
    level: str = "full"          # "none" | "full"
    always: bool = True          # web approvals default to durable


@router.get("/connections")
def api_list_connections(status: str = ""):
    """All connector connections (or ?status=pending for ones awaiting you)."""
    return {"connections": grants.list_grants(status or None)}


@router.post("/connections/{grant_id}/approve")
def api_approve_connection(grant_id: int, body: _ApproveConnIn):
    """Approve (or change the level of) a connection. Grants read, and, at
    level=full, write. Immediate: the corpus gate consults it on every call."""
    try:
        out = grants.approve(grant_id, body.level, always=body.always,
                             by="web-owner")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if out is None:
        raise HTTPException(404, "connection not found")
    return out


@router.post("/connections/{grant_id}/revoke")
def api_revoke_connection(grant_id: int):
    """Disconnect: revoke the grant + its outstanding tokens."""
    out = grants.revoke(grant_id)
    if out is None:
        raise HTTPException(404, "connection not found")
    return {"ok": True, **out}


# -- /api/pi (CMD-channel routes, names kept for SPA compatibility) ----

@router.get("/pi/online")
async def pi_online():
    result = await _core_get_json("/health", read=3.0)
    return {"online": bool(result.get("ok"))}


@router.get("/pi/status")
async def pi_status():
    health = await _core_get_json("/health", read=5.0)
    if not health.get("ok"):
        return {"online": False,
                "error": health.get("error", "core health check failed")}
    raw = await _send_command("status")
    return {"health": health, "status": _parse_command("status", raw).get("data"),
            "online": True}


@router.post("/pi/notes")
async def pi_create_note(request: Request):
    body = await _json_or_empty(request)
    payload = {"content": body.get("content", "")}
    for key in ("tags", "project", "note_type"):
        if body.get(key):
            payload[key] = body[key]
    return await _send_command("note", payload)


@router.get("/pi/notes")
async def pi_list_notes(limit: int = 20):
    return await _send_command("query", {
        "table": "notes", "filters": "", "limit": limit,
        "order_by": "created_at DESC"})


@router.post("/pi/cmd")
async def pi_cmd(request: Request):
    body = await _json_or_empty(request)
    command = body.get("command", "")
    if not command:
        raise HTTPException(400, "command required")
    return await _send_command(command, body.get("payload"))


@router.post("/pi/query")
async def pi_query(request: Request):
    body = await _json_or_empty(request)
    return await _send_command("query", {
        "table": body.get("table", ""),
        "filters": body.get("filters", ""),
        "limit": body.get("limit", 20),
        "order_by": body.get("order_by", "created_at DESC")})


# -- /api/data ---------------------------------------------------------

@router.get("/data/tables")
async def data_tables():
    raw = await _send_command("table_counts")
    return _parse_command("table_counts", raw)


@router.post("/data/query")
async def data_query(request: Request):
    body = await _json_or_empty(request)
    filters = body.get("filters")
    payload = {
        "table": body.get("table", ""),
        "filters": json.dumps(filters) if isinstance(filters, dict) else "",
        "limit": body.get("limit", 50),
        "order_by": body.get("order_by", "created_at DESC")}
    raw = await _send_command("query", payload)
    response = raw.get("response", "")
    if isinstance(response, str) and response.startswith("RSP:query:"):
        try:
            rows = json.loads(response[len("RSP:query:"):])
            return {"rows": rows, "count": len(rows)}
        except (json.JSONDecodeError, ValueError):
            pass
    return {"rows": [], "count": 0,
            "error": raw.get("error", "Unknown error")}


@router.post("/data/upsert")
async def data_upsert(request: Request):
    body = await _json_or_empty(request)
    raw = await _send_command("upsert", {
        "table": body.get("table", ""), "data": body.get("data", {})})
    return _parse_command("upsert", raw)


@router.post("/data/delete")
async def data_delete(request: Request):
    body = await _json_or_empty(request)
    raw = await _send_command("delete", {
        "table": body.get("table", ""), "id": body.get("id")})
    return _parse_command("delete", raw)


# -- /api/overseer catch-all -------------------------------------------

async def _json_or_empty(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@router.post("/overseer/chat/upload")
async def chat_upload(files: list[UploadFile] = File(...)):
    """Chat-attachment upload (A2). Parses multipart from the browser,
    validates each file (count/size/extension), and streams the raw
    bytes to the core's /files/uploads with the X-Filename protocol,
    returning the ChatAttachmentRef shape the SPA expects. Declared
    BEFORE the catch-all so it wins route matching for this exact path;
    the router-level _require_login dependency (owner pin + same-site
    CSRF on this POST) still applies."""
    if not files:
        raise HTTPException(400, "no files")
    if len(files) > _UPLOAD_MAX_FILES:
        raise HTTPException(400, f"too many files (max {_UPLOAD_MAX_FILES})")
    s = get_settings()
    attachments: list[dict] = []
    rejected: list[dict] = []
    async with httpx.AsyncClient(
            timeout=_timeout(_UPLOAD_TIMEOUT),
            auth=(s.core_username, s.core_token)) as client:
        for up in files:
            name = up.filename or "file"
            ext = os.path.splitext(name)[1].lower()
            if ext not in _ALLOWED_EXTS:
                rejected.append({"filename": name, "size": up.size or 0,
                                 "error": f"unsupported file type ({ext or 'none'})"})
                continue
            # Reject oversized files BEFORE reading the body into memory
            # when the multipart part reports its size (Starlette sets
            # UploadFile.size); the post-read check below is the backstop.
            if up.size is not None and up.size > _UPLOAD_MAX_BYTES:
                rejected.append({"filename": name, "size": up.size,
                                 "error": "file too large (max 5 MB)"})
                continue
            body = await up.read()
            if len(body) == 0:
                rejected.append({"filename": name, "size": 0,
                                 "error": "empty file"})
                continue
            if len(body) > _UPLOAD_MAX_BYTES:
                rejected.append({"filename": name, "size": len(body),
                                 "error": "file too large (max 5 MB)"})
                continue
            digest = hashlib.sha256(body).hexdigest()
            try:
                resp = await client.post(
                    f"{s.core_url}/files/uploads", content=body,
                    headers={"Content-Type": "application/octet-stream",
                             "X-Filename": name,
                             "X-Description": "Overseer chat attachment",
                             "X-Tags": "chat-attachment,overseer"})
                resp.raise_for_status()
                out = resp.json()
            except Exception as e:  # httpx errors, non-latin1 header, bad JSON
                rejected.append({"filename": name, "size": len(body),
                                 "error": f"upload failed: {type(e).__name__}"})
                continue
            mime = up.content_type or ""
            attachments.append({
                "filename": out.get("filename", name),
                "size": out.get("size", len(body)),
                "pi_path": out.get("path"),
                "file_id": out.get("file_id"),
                "sha256": digest,
                "mime_type": mime,
                "kind": _classify_kind(name, mime)})
    return {"ok": True, "attachments": attachments, "rejected": rejected,
            "counts": {"uploaded": len(attachments), "rejected": len(rejected)}}


# -- cloud voice (A3): Groq STT + ElevenLabs TTS -----------------------

_STT_MAX_BYTES = 25 * 1024 * 1024  # Groq's per-request audio cap
_GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_GROQ_STT_MODEL = "whisper-large-v3"
_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_DEFAULT_ELEVENLABS_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"


@router.post("/voice/stt")
async def voice_stt(file: UploadFile = File(...)):
    """Transcribe one spoken clip via Groq (cloud STT). Returns the
    desktop backend's shape {ok, text, duration_s, latency_ms} so the
    chat voice mode works unchanged from the browser."""
    key = get_settings().groq_api_key
    if not key:
        raise HTTPException(501, "cloud voice STT is not configured")
    body = await file.read()
    if not body:
        raise HTTPException(400, "empty clip")
    if len(body) > _STT_MAX_BYTES:
        raise HTTPException(413, "clip too large")
    files = {"file": (file.filename or "clip.webm", body,
                      file.content_type or "audio/webm")}
    try:
        async with httpx.AsyncClient(timeout=_timeout(60.0)) as client:
            resp = await client.post(
                _GROQ_STT_URL,
                headers={"Authorization": f"Bearer {key}"},
                data={"model": _GROQ_STT_MODEL}, files=files)
        resp.raise_for_status()
        text = (resp.json().get("text") or "").strip()
    except Exception as e:
        raise HTTPException(502, f"STT failed: {type(e).__name__}")
    return {"ok": True, "text": text, "duration_s": None, "latency_ms": None}


class _TtsRequest(BaseModel):
    text: str
    voice_id: str | None = None


@router.post("/voice/tts")
async def voice_tts(body: _TtsRequest):
    """Synthesize speech via ElevenLabs (cloud TTS). Returns audio/mpeg,
    or 200 {ok:false, reason} when no key is set so the SPA falls back to
    the browser's on-device speechSynthesis (same contract as desktop)."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    s = get_settings()
    if not s.elevenlabs_api_key:
        return {"ok": False, "reason": "elevenlabs_not_configured",
                "message": "No ElevenLabs key set - using on-device voice."}
    voice_id = body.voice_id or s.elevenlabs_voice_id or _DEFAULT_ELEVENLABS_VOICE
    payload = json.dumps({
        "text": text, "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode()
    try:
        async with httpx.AsyncClient(timeout=_timeout(30.0)) as client:
            resp = await client.post(
                _ELEVENLABS_TTS_URL.format(voice_id=voice_id), content=payload,
                headers={"xi-api-key": s.elevenlabs_api_key,
                         "Content-Type": "application/json",
                         "Accept": "audio/mpeg"})
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"TTS failed: {type(e).__name__}")
    return Response(content=resp.content, media_type="audio/mpeg")


@router.get("/voice/config")
async def voice_config():
    """Which voice backends the cloud Hub offers. No on-device whisper
    in the cloud; browser speechSynthesis is always available."""
    s = get_settings()
    groq = bool(s.groq_api_key)
    eleven = bool(s.elevenlabs_api_key)
    return {
        "ok": True,
        "stt": {"on_device_available": False, "whisper_model": "",
                "groq_configured": groq},
        "tts": {"on_device_available": True,
                "elevenlabs_configured": eleven,
                "elevenlabs_voice_id": s.elevenlabs_voice_id},
        "preferred_stt": "groq" if groq else "on-device",
        "preferred_tts": "elevenlabs" if eleven else "on-device",
    }


@router.api_route("/overseer/{path:path}", methods=["GET", "POST", "DELETE"])
async def overseer_facade(path: str, request: Request):
    path = path.strip("/")
    # Confinement: the catch-all must only reach /plugins/overseer/*.
    # httpx normalizes ../ dot-segments out of the URL before sending,
    # so a decoded "../../files/db" would escape to the core's /files
    # and /api/cmd surface (service-token authed). Reject any traversal
    # or scheme-injection before building the upstream URL.
    if ".." in path.split("/") or "\\" in path or "://" in path:
        raise HTTPException(400, "invalid overseer path")
    if path in _DESKTOP_ONLY:
        return _DESKTOP_ONLY[path]

    s = get_settings()
    read = _LONG_READS.get(path, _DEFAULT_READ)
    url = f"{s.core_url}/plugins/overseer/{path}"

    # The one method translation in the desktop backend: the SPA does
    # GET /vector/search?q=&k= but the core route is POST.
    if path == "vector/search" and request.method == "GET":
        method = "POST"
        params: dict = {}
        body_bytes = json.dumps({
            "q": request.query_params.get("q", ""),
            "k": request.query_params.get("k", "8"),
        }).encode()
        content_type = "application/json"
    else:
        method = request.method
        # Drop empty-valued params: the desktop backend omits unset keys
        # entirely and core handlers rely on their own defaults.
        params = {k: v for k, v in request.query_params.items() if v != ""}
        body_bytes = await request.body()
        content_type = request.headers.get("content-type", "application/json")

    try:
        async with httpx.AsyncClient(
                timeout=_timeout(read),
                auth=(s.core_username, s.core_token)) as client:
            upstream = await client.request(
                method, url, params=params,
                content=body_bytes if body_bytes else None,
                headers={"Content-Type": content_type})
    except httpx.TimeoutException:
        return {"ok": False, "error": "core request timed out"}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Cannot reach core: {type(e).__name__}"}

    ctype = upstream.headers.get("content-type", "")
    if "json" not in ctype:
        return {"ok": False,
                "error": f"core returned {upstream.status_code} non-JSON"}
    # Desktop-backend contract: the SPA sees HTTP 200 and reads ok/error
    # from the body, so upstream JSON passes through under a 200.
    return Response(content=upstream.content, media_type="application/json")
