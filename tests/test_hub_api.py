"""Cloud Hub /api facade (Phase A) - auth gate, forwarding contracts,
desktop-backend response fidelity, and the SPA static mount.

The core is faked at the httpx.AsyncClient seam so the tests exercise
the facade's real request-building (URLs, params, bodies, timeouts,
service-token auth) without a live core process.
"""
from __future__ import annotations

import json

import httpx
import pytest
from starlette.requests import Request

# The owner's authenticated same-origin browser: owner-oid principal +
# a matching Origin so state-changing requests pass the CSRF guard.
# Defined after the helpers below.


class FakeResponse:
    def __init__(self, payload, status_code=200,
                 content_type="application/json"):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"status {self.status_code}")


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient. Canned responses are keyed by
    (METHOD, url suffix); every call is recorded for assertions."""

    calls: list = []
    ctor_kwargs: list = []
    responses: dict = {}

    def __init__(self, **kwargs):
        FakeAsyncClient.ctor_kwargs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kw):
        FakeAsyncClient.calls.append({"method": method, "url": url, **kw})
        for (m, suffix), payload in FakeAsyncClient.responses.items():
            if m == method and url.endswith(suffix):
                return payload if isinstance(payload, FakeResponse) \
                    else FakeResponse(payload)
        return FakeResponse({"ok": True})

    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url, **kw):
        return await self.request("POST", url, **kw)


# A base64 principal carrying a specific Entra object id.
def _principal(oid: str) -> dict:
    import base64
    claims = [{"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
               "val": oid}]
    blob = base64.b64encode(
        json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()
    return {"x-ms-client-principal": blob}


OWNER_OID = "11111111-1111-1111-1111-111111111111"
OTHER_OID = "22222222-2222-2222-2222-222222222222"
# Same-origin browser header for write requests (CSRF guard).
ORIGIN = {"origin": "https://hub.test"}
# The owner's authenticated same-origin browser: used by every existing
# test. Origin is harmless on reads and satisfies the CSRF guard on
# writes, so one header set covers both.
PRINCIPAL = {**_principal(OWNER_OID), **ORIGIN}


@pytest.fixture()
def webui(tmp_path, monkeypatch):
    """Minimal web-UI app: HumanLoginMiddleware + the facade + /intro +
    a tiny SPA dir mounted at /. Avoids full create_app (the MCP session
    manager can only run once per process). Owner allowlist ON."""
    static = tmp_path / "spa"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>CORTEX HUB SPA</html>",
                                       encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)",
                                              encoding="utf-8")
    monkeypatch.setenv("GATEWAY_STATIC_DIR", str(static))
    monkeypatch.setenv("CORTEX_SERVICE_TOKEN", "tok123")
    monkeypatch.setenv("CORTEX_CORE_URL", "http://core.test:8420")
    monkeypatch.setenv("GATEWAY_OWNER_OIDS", OWNER_OID)
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "https://hub.test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-key")

    from cortex_gateway import config
    config.get_settings.cache_clear()

    from cortex_gateway.rest import hub_api
    monkeypatch.setattr(hub_api.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.calls = []
    FakeAsyncClient.ctor_kwargs = []
    FakeAsyncClient.responses = {}

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.staticfiles import StaticFiles
    from cortex_gateway.app import HumanLoginMiddleware
    from cortex_gateway.rest import intro_page

    app = FastAPI()
    app.add_middleware(HumanLoginMiddleware)
    app.include_router(hub_api.router)
    app.include_router(intro_page.router)
    app.mount("/", StaticFiles(directory=str(static), html=True), name="hub")

    client = TestClient(app, follow_redirects=False)
    yield client
    config.get_settings.cache_clear()


# -- auth gate ---------------------------------------------------------

def test_api_requires_principal(webui):
    assert webui.get("/api/pi/online").status_code == 401
    assert webui.post("/api/data/query", json={"table": "notes"}).status_code == 401


def test_root_redirects_to_entra_login(webui):
    resp = webui.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/.auth/login/aad")


def test_intro_redirects_without_login(webui):
    resp = webui.get("/intro")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/.auth/login/aad")


def test_root_serves_spa_with_principal(webui):
    resp = webui.get("/", headers=PRINCIPAL)
    assert resp.status_code == 200
    assert "CORTEX HUB SPA" in resp.text


def test_assets_are_public(webui):
    # The bundle is built from a public repo; data flows only via /api.
    assert webui.get("/assets/app.js").status_code == 200


def test_intro_serves_with_principal(webui):
    resp = webui.get("/intro", headers=PRINCIPAL)
    assert resp.status_code == 200
    assert "Cortex Context" in resp.text


# -- health surfaces ---------------------------------------------------

def test_api_health_reports_cloud_mode(webui):
    body = webui.get("/api/health", headers=PRINCIPAL).json()
    assert body["mode"] == "cloud"
    assert body["status"] == "ok"


def test_chat_health_reports_no_lmstudio(webui):
    body = webui.get("/api/chat/health", headers=PRINCIPAL).json()
    assert body["lmstudio_online"] is False


def test_pi_online_uses_core_health(webui):
    FakeAsyncClient.responses[("GET", "/health")] = {"ok": True}
    body = webui.get("/api/pi/online", headers=PRINCIPAL).json()
    assert body == {"online": True}
    assert FakeAsyncClient.calls[0]["url"] == "http://core.test:8420/health"
    assert FakeAsyncClient.ctor_kwargs[0]["auth"] == ("cortex", "tok123")


# -- overseer catch-all ------------------------------------------------

def test_overseer_forward_drops_empty_params(webui):
    FakeAsyncClient.responses[("GET", "/plugins/overseer/status")] = {
        "ok": True, "loop": "idle"}
    resp = webui.get("/api/overseer/status?days=5&source=",
                     headers=PRINCIPAL)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "loop": "idle"}
    call = FakeAsyncClient.calls[0]
    assert call["url"] == "http://core.test:8420/plugins/overseer/status"
    assert call["params"] == {"days": "5"}


def test_vector_search_get_becomes_post(webui):
    webui.get("/api/overseer/vector/search?q=hello&k=3", headers=PRINCIPAL)
    call = FakeAsyncClient.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/plugins/overseer/vector/search")
    assert json.loads(call["content"]) == {"q": "hello", "k": "3"}


def test_desktop_only_routes_stub_without_forwarding(webui):
    # Shape-compatible empty successes (the SPA reads found/total/counts
    # without checking ok), so the panels show "nothing to do" not an error.
    scan = webui.get("/api/overseer/scan/claude-code", headers=PRINCIPAL).json()
    assert scan["ok"] is True and scan["total"] == 0 and scan["found"] == []
    imp = webui.post("/api/overseer/import", headers=PRINCIPAL,
                     json={"paths": []}).json()
    assert imp["ok"] is True and imp["counts"]["imported"] == 0
    assert FakeAsyncClient.calls == []


# -- chat attachment upload (A2) --------------------------------------

def test_chat_upload_forwards_to_core_files(webui):
    FakeAsyncClient.responses[("POST", "/files/uploads")] = {
        "ok": True, "filename": "note.txt", "size": 5,
        "path": "/data/uploads/note.txt", "file_id": 42, "registered": True}
    resp = webui.post(
        "/api/overseer/chat/upload", headers=PRINCIPAL,
        files=[("files", ("note.txt", b"hello", "text/plain"))])
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"uploaded": 1, "rejected": 0}
    att = body["attachments"][0]
    assert att["filename"] == "note.txt" and att["file_id"] == 42
    assert att["pi_path"] == "/data/uploads/note.txt"
    assert att["kind"] == "text"
    assert att["sha256"] == __import__("hashlib").sha256(b"hello").hexdigest()
    call = FakeAsyncClient.calls[0]
    assert call["url"].endswith("/files/uploads")
    assert call["headers"]["X-Filename"] == "note.txt"
    assert call["headers"]["X-Tags"] == "chat-attachment,overseer"
    assert call["content"] == b"hello"


def test_chat_upload_rejects_bad_ext_and_empty_without_forwarding(webui):
    resp = webui.post(
        "/api/overseer/chat/upload", headers=PRINCIPAL,
        files=[("files", ("virus.exe", b"MZ", "application/octet-stream")),
               ("files", ("empty.txt", b"", "text/plain"))])
    body = resp.json()
    assert body["counts"]["uploaded"] == 0
    assert len(body["rejected"]) == 2
    errs = " ".join(r["error"] for r in body["rejected"])
    assert "unsupported file type" in errs and "empty file" in errs
    # Neither invalid file was forwarded to the core.
    assert FakeAsyncClient.calls == []


def test_chat_upload_too_many_files(webui):
    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(11)]
    resp = webui.post("/api/overseer/chat/upload", headers=PRINCIPAL,
                      files=files)
    assert resp.status_code == 400


def test_chat_upload_is_owner_and_csrf_gated(webui):
    # Router-level dependency applies: non-owner 403, cross-site 403.
    r1 = webui.post("/api/overseer/chat/upload",
                    headers=_principal(OTHER_OID),
                    files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r1.status_code == 403
    r2 = webui.post("/api/overseer/chat/upload",
                    headers={**_principal(OWNER_OID),
                             "origin": "https://evil.example"},
                    files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r2.status_code == 403
    assert FakeAsyncClient.calls == []


def test_overseer_path_traversal_rejected(webui):
    # Encoded dot-segments arrive at the ASGI layer as ".." and hit the
    # 400 guard; raw "../.." is collapsed by the client/proxy before it
    # arrives (404 on the minimal app). Either way the security property
    # holds: a traversal NEVER forwards to the core.
    for evil in ("%2e%2e/%2e%2e/files/db", "..%2f..%2ffiles",
                 "%2e%2e/api/cmd"):
        resp = webui.get(f"/api/overseer/{evil}", headers=PRINCIPAL)
        assert resp.status_code in (400, 404), f"{evil} -> {resp.status_code}"
    assert FakeAsyncClient.calls == []


def test_plugins_stub_returns_empty_list(webui):
    body = webui.get("/api/plugins", headers=PRINCIPAL).json()
    assert body == []


# -- cloud voice (A3) --------------------------------------------------

def test_voice_config_reports_cloud_backends(webui):
    body = webui.get("/api/voice/config", headers=PRINCIPAL).json()
    assert body["stt"]["on_device_available"] is False
    assert body["stt"]["groq_configured"] is True
    assert body["tts"]["elevenlabs_configured"] is True
    assert body["preferred_stt"] == "groq"


def test_voice_stt_forwards_to_groq(webui):
    FakeAsyncClient.responses[("POST", "audio/transcriptions")] = {
        "text": "hello world"}
    resp = webui.post("/api/voice/stt", headers=PRINCIPAL,
                      files=[("file", ("clip.webm", b"RIFFxxxx", "audio/webm"))])
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "text": "hello world",
                           "duration_s": None, "latency_ms": None}
    call = FakeAsyncClient.calls[0]
    assert call["url"].endswith("audio/transcriptions")
    assert call["headers"]["Authorization"] == "Bearer groq-test-key"


def test_voice_tts_returns_audio(webui):
    FakeAsyncClient.responses[("POST", "text-to-speech/" +
                               "21m00Tcm4TlvDq8ikWAM")] = FakeResponse(
        {"audio": "x"}, content_type="audio/mpeg")
    resp = webui.post("/api/voice/tts", headers=PRINCIPAL,
                      json={"text": "say this"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    call = FakeAsyncClient.calls[0]
    assert call["headers"]["xi-api-key"] == "el-test-key"


def test_voice_agent_status_unavailable_in_cloud(webui):
    body = webui.get("/api/voice/agent/status", headers=PRINCIPAL).json()
    assert body["running"] is False and body["reason"] == "desktop_only"


def test_voice_stt_501_without_key(webui, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from cortex_gateway import config
    config.get_settings.cache_clear()
    resp = webui.post("/api/voice/stt", headers=PRINCIPAL,
                      files=[("file", ("clip.webm", b"x", "audio/webm"))])
    assert resp.status_code == 501


def test_voice_tts_falls_back_without_key(webui, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    from cortex_gateway import config
    config.get_settings.cache_clear()
    body = webui.post("/api/voice/tts", headers=PRINCIPAL,
                      json={"text": "hi"}).json()
    assert body["ok"] is False and body["reason"] == "elevenlabs_not_configured"


# -- owner authorization ----------------------------------------------

def test_non_owner_principal_is_forbidden(webui):
    resp = webui.get("/api/health", headers=_principal(OTHER_OID))
    assert resp.status_code == 403
    # and it never reaches the core
    resp = webui.get("/api/overseer/status", headers=_principal(OTHER_OID))
    assert resp.status_code == 403
    assert FakeAsyncClient.calls == []


def test_owner_principal_allowed(webui):
    assert webui.get("/api/health", headers=_principal(OWNER_OID)).status_code == 200


def test_owner_pin_falls_back_to_presence_when_unset(tmp_path, monkeypatch):
    # No GATEWAY_OWNER_OIDS: any authenticated principal passes (fresh
    # friend-deploy before the owner is configured).
    from cortex_gateway.identity import owner_ok
    assert owner_ok("anything", frozenset()) is True
    assert owner_ok("", frozenset()) is False
    assert owner_ok(next(iter(_principal(OWNER_OID).values())),
                    frozenset({OWNER_OID})) is True
    assert owner_ok(next(iter(_principal(OTHER_OID).values())),
                    frozenset({OWNER_OID})) is False


# -- CSRF guard on writes ---------------------------------------------

def test_cross_site_write_blocked(webui):
    resp = webui.post("/api/data/upsert",
                      headers={**_principal(OWNER_OID),
                               "origin": "https://evil.example"},
                      json={"table": "notes", "data": {}})
    assert resp.status_code == 403
    assert FakeAsyncClient.calls == []


def test_write_without_origin_blocked(webui):
    resp = webui.post("/api/data/upsert", headers=_principal(OWNER_OID),
                      json={"table": "notes", "data": {}})
    assert resp.status_code == 403


def test_same_site_write_allowed(webui):
    FakeAsyncClient.responses[("POST", "/api/cmd")] = {
        "ok": True, "response": 'RSP:upsert:{"table":"notes","id":1}'}
    resp = webui.post("/api/data/upsert", headers=PRINCIPAL,
                      json={"table": "notes", "data": {"content": "x"}})
    assert resp.status_code == 200


def test_read_needs_no_origin(webui):
    # GET is exempt from the CSRF guard (no state change).
    assert webui.get("/api/health",
                     headers=_principal(OWNER_OID)).status_code == 200


def test_long_route_gets_long_read_timeout(webui):
    webui.post("/api/overseer/backfill", headers=PRINCIPAL,
               json={"kind": "gists"})
    assert FakeAsyncClient.ctor_kwargs[0]["timeout"].read == 600.0


def test_non_json_upstream_becomes_error_dict(webui):
    FakeAsyncClient.responses[("GET", "/plugins/overseer/status")] = \
        FakeResponse({"boom": 1}, status_code=502, content_type="text/html")
    body = webui.get("/api/overseer/status", headers=PRINCIPAL).json()
    assert body["ok"] is False
    assert "502" in body["error"]


# -- CMD-channel routes ------------------------------------------------

def test_data_query_parses_rsp_envelope(webui):
    FakeAsyncClient.responses[("POST", "/api/cmd")] = {
        "ok": True, "response": 'RSP:query:[{"id": 1}]'}
    body = webui.post("/api/data/query", headers=PRINCIPAL,
                      json={"table": "notes",
                            "filters": {"project": "cortex"}}).json()
    assert body == {"rows": [{"id": 1}], "count": 1}
    sent = FakeAsyncClient.calls[0]["json"]
    assert sent["command"] == "query"
    assert sent["payload"]["table"] == "notes"
    assert json.loads(sent["payload"]["filters"]) == {"project": "cortex"}


def test_data_tables_parses_table_counts(webui):
    FakeAsyncClient.responses[("POST", "/api/cmd")] = {
        "ok": True, "response": 'RSP:table_counts:{"notes": 12}'}
    body = webui.get("/api/data/tables", headers=PRINCIPAL).json()
    assert body == {"data": {"notes": 12}}


def test_pi_notes_get_returns_raw_envelope(webui):
    # Contract fidelity: this route is deliberately UNPARSED (the SPA
    # expects the raw envelope here and the parsed shape on /api/data).
    FakeAsyncClient.responses[("POST", "/api/cmd")] = {
        "ok": True, "response": 'RSP:query:[]'}
    body = webui.get("/api/pi/notes", headers=PRINCIPAL).json()
    assert body == {"ok": True, "response": "RSP:query:[]"}


def test_pi_cmd_requires_command(webui):
    assert webui.post("/api/pi/cmd", headers=PRINCIPAL,
                      json={}).status_code == 400


# -- rate-limit key ----------------------------------------------------

def _principal_scope():
    return {"type": "http", "method": "GET", "path": "/api/health",
            "headers": [(b"x-ms-client-principal", b"e30="),
                        (b"x-forwarded-for", b"9.9.9.9")],
            "query_string": b"", "client": ("10.0.0.1", 1)}


def test_principal_gets_authenticated_bucket_in_web_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_STATIC_DIR", str(tmp_path))
    from cortex_gateway import config
    config.get_settings.cache_clear()
    from cortex_gateway.app import _client_key
    key, authed = _client_key(Request(_principal_scope()))
    assert authed is True
    assert key == "human:9.9.9.9"
    config.get_settings.cache_clear()


def test_forged_principal_ignored_without_web_ui(monkeypatch):
    # finding 4: on a Pi/tunnel deployment (web_ui off) nothing strips the
    # header, so a forged x-ms-client-principal must NOT reach the auth bucket.
    monkeypatch.delenv("GATEWAY_STATIC_DIR", raising=False)
    from cortex_gateway import config
    config.get_settings.cache_clear()
    from cortex_gateway.app import _client_key
    key, authed = _client_key(Request(_principal_scope()))
    assert authed is False
    assert key == "ip:9.9.9.9"
    config.get_settings.cache_clear()


def test_anonymous_stays_anonymous_bucket():
    from cortex_gateway.app import _client_key
    scope = {"type": "http", "method": "GET", "path": "/api/health",
             "headers": [(b"x-forwarded-for", b"9.9.9.9")],
             "query_string": b"", "client": ("10.0.0.1", 1)}
    key, authed = _client_key(Request(scope))
    assert authed is False
    assert key == "ip:9.9.9.9"


# -- web-UI off = no facade -------------------------------------------

def test_facade_absent_without_static_dir(tmp_path, monkeypatch):
    db_file = tmp_path / "gw.db"
    monkeypatch.setenv("DB_URL", "sqlite:///" + str(db_file).replace("\\", "/"))
    monkeypatch.delenv("GATEWAY_STATIC_DIR", raising=False)

    from cortex_gateway import config, db
    config.get_settings.cache_clear()
    assert config.get_settings().web_ui is False

    import importlib
    db.engine.cache_clear()
    importlib.reload(db)
    db.init_schema()

    from cortex_gateway.app import create_app
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/health" not in paths
    assert "/intro" not in paths
    assert not any(getattr(r, "name", "") == "hub" for r in app.routes)
    config.get_settings.cache_clear()
