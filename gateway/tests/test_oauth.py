"""OAuth 2.1 + PKCE server - best-practice guards.

Covers the hardening the security audit called for (report in
`_audit_backups/GATEWAY_SECURITY_AUDIT_2026-06-23.md`) plus the OAuth 2.1
best practices layered on top:

  - scope never escalates to admin/app via OAuth (comma AND space delimited)
  - S256-only PKCE, constant-time verify
  - https-only redirect URIs
  - discovery advertises S256 + none auth + RFC 9207 issuer identification
  - authorization codes are single-use *atomically* (replay race closed)
  - OAuth-minted access tokens are short-lived (finite TTL), not immortal
"""
import base64
import hashlib
import logging

import pytest


def _oauth_app():
    """Minimal app carrying only the OAuth router + human-login gate. Avoids
    mounting the MCP surface, whose session manager can only .run() once per
    process (so multiple full create_app() instances collide across tests)."""
    from fastapi import FastAPI
    from cortex_gateway import oauth
    from cortex_gateway.app import HumanLoginMiddleware
    app = FastAPI()
    app.add_middleware(HumanLoginMiddleware)
    app.include_router(oauth.router)
    return app


# ── scope hardening (audit finding 2) ─────────────────────────────────

def _allow_write(monkeypatch, on=True):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOW_WRITE", "1" if on else "")
    from cortex_gateway import config
    config.get_settings.cache_clear()


def _allow_loopback(monkeypatch, on=True):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOW_LOOPBACK", "1" if on else "")
    from cortex_gateway import config
    config.get_settings.cache_clear()


def test_clean_scope_drops_admin_and_app(monkeypatch):
    _allow_write(monkeypatch)   # isolate admin/app dropping from the write cap
    from cortex_gateway import oauth
    assert oauth._clean_scope("connector:write,admin") == "connector:write"
    assert oauth._clean_scope("connector:read app") == "connector:read"
    assert oauth._clean_scope("admin") == "connector:read"          # default
    assert oauth._clean_scope("") == "connector:read"


def test_clean_scope_dedups_and_keeps_order(monkeypatch):
    _allow_write(monkeypatch)
    from cortex_gateway import oauth
    assert oauth._clean_scope("connector:read connector:read connector:write") \
        == "connector:read connector:write"


def test_clean_scope_readonly_by_default(monkeypatch):
    # Connectors are read-only unless the deployment opts into write.
    monkeypatch.delenv("GATEWAY_OAUTH_ALLOW_WRITE", raising=False)
    from cortex_gateway import config, oauth
    config.get_settings.cache_clear()
    assert oauth._clean_scope("connector:read connector:write") == "connector:read"
    assert oauth._clean_scope("connector:write,admin") == "connector:read"
    assert oauth._clean_scope("connector:write") == "connector:read"
    config.get_settings.cache_clear()


def test_multi_scope_token_usable_either_delimiter(gw):
    # Regression: gateway_tokens.scopes is comma-delimited but the OAuth layer
    # emits space-delimited. A token granted BOTH connector scopes must resolve
    # to two usable scopes regardless of the stored delimiter.
    _config, _db, _oauth = gw
    from cortex_gateway import auth
    for stored in ("connector:read,connector:write",
                   "connector:read connector:write"):
        raw = auth.mint(name="multi", scopes=stored, kind="oauth")
        p = auth.principal_from_bearer(f"Bearer {raw}")
        assert p.has("connector:read") and p.has("connector:write"), stored


# ── PKCE (S256 only, constant-time) ───────────────────────────────────

def _challenge(verifier: str) -> str:
    d = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(d).decode().rstrip("=")


def test_pkce_s256_roundtrip():
    from cortex_gateway import oauth
    v = "a" * 64
    assert oauth._verify_pkce(v, _challenge(v))
    assert not oauth._verify_pkce(v, _challenge("different"))


# ── redirect URI validation (audit finding 3) ─────────────────────────

def test_valid_redirect_https_only():
    from cortex_gateway import oauth
    assert oauth._valid_redirect("https://claude.ai/api/mcp/auth_callback")
    assert not oauth._valid_redirect("http://claude.ai/cb")
    assert not oauth._valid_redirect("javascript:alert(1)")
    assert not oauth._valid_redirect("myapp://cb")
    assert not oauth._valid_redirect("not a url")


# ── discovery metadata ────────────────────────────────────────────────

def test_as_metadata_advertises_best_practices():
    from starlette.requests import Request
    from cortex_gateway import oauth
    scope = {"type": "http", "headers": [], "path": "/", "method": "GET",
             "scheme": "https", "server": ("gw.example.com", 443),
             "query_string": b""}
    meta = oauth.as_metadata(Request(scope))
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
    assert meta["grant_types_supported"] == ["authorization_code"]
    assert meta["authorization_response_iss_parameter_supported"] is True
    assert "admin" not in meta["scopes_supported"]
    assert "app" not in meta["scopes_supported"]


# ── trusted-client redirect allowlist (consent-phishing defense) ──────

_CLAUDE = "https://claude.ai/api/mcp/auth_callback"
_CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"


def test_redirect_allowed_open_when_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", raising=False)
    from cortex_gateway import config, oauth
    config.get_settings.cache_clear()
    # Empty allowlist -> any valid https redirect is allowed (rollout phase).
    assert oauth._redirect_allowed("https://anything.example/cb")
    assert not oauth._redirect_allowed("http://insecure/cb")   # still https-only


def test_redirect_allowed_exact_match_when_set(monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", f"{_CLAUDE} {_CHATGPT}")
    from cortex_gateway import config, oauth
    config.get_settings.cache_clear()
    assert oauth._redirect_allowed(_CLAUDE)
    assert oauth._redirect_allowed(_CHATGPT)
    assert not oauth._redirect_allowed("https://claude.ai/api/mcp/auth_callback/evil")
    assert not oauth._redirect_allowed("https://attacker.example/cb")
    config.get_settings.cache_clear()


def test_register_enforces_allowlist(gw, monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", f"{_CLAUDE} {_CHATGPT}")
    _config, _db, _oauth = gw
    _config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        # Trusted callbacks register; an attacker-controlled one is rejected.
        assert client.post("/oauth/register", json={
            "client_name": "claude", "redirect_uris": [_CLAUDE]}).status_code == 201
        assert client.post("/oauth/register", json={
            "client_name": "chatgpt", "redirect_uris": [_CHATGPT]}).status_code == 201
        r = client.post("/oauth/register", json={
            "client_name": "evil", "redirect_uris": ["https://attacker.example/cb"]})
        assert r.status_code == 400 and "allowlist" in r.text
        # A mixed batch (one good, one bad) is rejected wholesale.
        assert client.post("/oauth/register", json={
            "client_name": "mixed",
            "redirect_uris": [_CLAUDE, "https://attacker.example/cb"]}).status_code == 400
    _config.get_settings.cache_clear()


def test_register_dedups_same_identity(gw, monkeypatch):
    # A connector re-registering with the same name + redirects reuses its
    # client_id, so one service is one connection instead of a new row per
    # connect. A different service still gets a distinct client_id.
    monkeypatch.delenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", raising=False)
    _config, _db, _oauth = gw
    _config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        body = {"client_name": "Claude", "redirect_uris": [_CLAUDE]}
        first = client.post("/oauth/register", json=body).json()["client_id"]
        second = client.post("/oauth/register", json=body).json()["client_id"]
        assert first == second
        other = client.post("/oauth/register", json={
            "client_name": "ChatGPT", "redirect_uris": [_CHATGPT]}).json()["client_id"]
        assert other != first
    _config.get_settings.cache_clear()


def test_authorize_blocks_grandfathered_client_after_lockdown(gw, monkeypatch):
    # A client registered while the allowlist was empty must not be usable with
    # its non-listed redirect once the allowlist is set (defense-in-depth).
    _config, db, _oauth = gw
    db.insert("oauth_clients", {
        "client_id": "cli_old", "client_name": "legacy",
        "redirect_uris": "https://attacker.example/cb"})
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", _CLAUDE)
    _config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        human = {"x-ms-client-principal": "e30="}
        r = client.get("/oauth/authorize", params={
            "client_id": "cli_old",
            "redirect_uri": "https://attacker.example/cb",
            "code_challenge": "x"}, headers=human, follow_redirects=False)
        assert r.status_code == 400 and "allowlist" in r.text
    _config.get_settings.cache_clear()


# ── RFC 8252 loopback redirects (Claude Code native app) ──────────────

def test_loopback_detection_and_gating(monkeypatch):
    from cortex_gateway import config, oauth
    _allow_loopback(monkeypatch, on=True)
    assert oauth._is_loopback_redirect("http://127.0.0.1:52345/callback")
    assert oauth._is_loopback_redirect("http://localhost:8080/callback")
    assert not oauth._is_loopback_redirect("http://evil.example/cb")   # non-loopback http
    assert not oauth._is_loopback_redirect("https://127.0.0.1/cb")     # https, not loopback path
    _allow_loopback(monkeypatch, on=False)
    assert not oauth._is_loopback_redirect("http://127.0.0.1:1/cb")    # gated off by default
    config.get_settings.cache_clear()


def test_loopback_allowed_even_with_allowlist_locked(monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", _CLAUDE)
    _allow_loopback(monkeypatch, on=True)
    from cortex_gateway import config, oauth
    assert oauth._redirect_allowed("http://127.0.0.1:52345/callback")  # loopback bypasses allowlist
    assert oauth._redirect_allowed(_CLAUDE)                            # listed https still ok
    assert not oauth._redirect_allowed("https://attacker.example/cb")  # non-listed https rejected
    assert not oauth._redirect_allowed("http://evil.example/cb")       # non-loopback http rejected
    config.get_settings.cache_clear()


def test_loopback_port_agnostic_match():
    # Claude Code may register a loopback redirect and authorize on a different
    # ephemeral port; those must match (RFC 8252), but path/host must still agree.
    from cortex_gateway import oauth
    assert oauth._redirects_match("http://localhost:52345/callback",
                                  "http://localhost/callback")
    assert oauth._redirects_match("http://127.0.0.1:1/cb", "http://127.0.0.1:2/cb")
    assert not oauth._redirects_match("http://localhost:1/callback",
                                      "http://localhost:1/other")       # path differs
    assert not oauth._redirects_match("http://localhost:1/cb",
                                      "http://127.0.0.1:1/cb")           # host differs


def test_register_accepts_loopback_when_enabled(gw, monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", _CLAUDE)   # locked down
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOW_LOOPBACK", "1")
    _config, _db, _oauth = gw
    _config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        # Claude Code loopback registers even with the https allowlist locked.
        assert client.post("/oauth/register", json={
            "client_name": "claude-code",
            "redirect_uris": ["http://127.0.0.1:52345/callback",
                              "http://localhost:52345/callback"]}).status_code == 201
        # A non-loopback http redirect is still rejected.
        assert client.post("/oauth/register", json={
            "client_name": "x",
            "redirect_uris": ["http://evil.example/cb"]}).status_code == 400
    _config.get_settings.cache_clear()


# ── hub (owner-device) elevated scope ─────────────────────────────────

_HUB_HOST = "phone.cortex.app"


def _run_flow(client, cid, redirect, scope, verifier):
    human = {"x-ms-client-principal": "e30="}
    r = client.get("/oauth/authorize", params={
        "client_id": cid, "redirect_uri": redirect,
        "code_challenge": _challenge(verifier), "scope": scope}, headers=human)
    nonce = r.text.split("consent=")[1].split('"')[0]
    r = client.get(f"/oauth/authorize?consent={nonce}", headers=human,
                   follow_redirects=False)
    code = r.headers["location"].split("code=")[1].split("&")[0]
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier})


def test_hub_redirect_mints_hub_scope_implying_app(gw, monkeypatch):
    monkeypatch.setenv("GATEWAY_HUB_REDIRECT_HOSTS", _HUB_HOST)
    _config, _db, _oauth = gw
    _config.get_settings.cache_clear()
    from cortex_gateway import auth
    from starlette.testclient import TestClient
    redirect = f"https://{_HUB_HOST}/cb"
    with TestClient(_oauth_app()) as client:
        cid = client.post("/oauth/register", json={
            "client_name": "phone", "redirect_uris": [redirect]}).json()["client_id"]
        r = _run_flow(client, cid, redirect, "connector:read", "v" * 43)
        assert r.status_code == 200 and r.json()["scope"] == "hub"
        p = auth.principal_from_bearer("Bearer " + r.json()["access_token"])
        assert p.has("app") is True            # hub implies app (REST + mgmt)
        assert p.is_connector is False         # not grant-gated
        assert p.has("connector:read") is False  # cannot use /mcp
    _config.get_settings.cache_clear()


def test_connector_can_never_obtain_hub(gw, monkeypatch):
    # A connector at a non-hub host that REQUESTS hub/app/admin gets none of them.
    monkeypatch.setenv("GATEWAY_HUB_REDIRECT_HOSTS", _HUB_HOST)
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", "https://grok.com/cb")
    _config, _db, _oauth = gw
    _config.get_settings.cache_clear()
    from cortex_gateway import auth
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        cid = client.post("/oauth/register", json={
            "client_name": "grok",
            "redirect_uris": ["https://grok.com/cb"]}).json()["client_id"]
        r = _run_flow(client, cid, "https://grok.com/cb", "hub app admin", "v" * 43)
        assert r.json()["scope"] == "connector:read"      # all elevation stripped
        p = auth.principal_from_bearer("Bearer " + r.json()["access_token"])
        assert p.has("app") is False and p.has("hub") is False
    _config.get_settings.cache_clear()


def test_hub_inert_by_default(monkeypatch):
    monkeypatch.delenv("GATEWAY_HUB_REDIRECT_HOSTS", raising=False)
    from cortex_gateway import config, oauth
    config.get_settings.cache_clear()
    assert oauth._is_hub_redirect(f"https://{_HUB_HOST}/cb") is False
    config.get_settings.cache_clear()


def test_hub_scope_implies_app_only(gw):
    from cortex_gateway.auth import Principal
    p = Principal(id=1, name="hub:x", kind="hub", scopes={"hub"},
                  max_tier="restricted", category_filter=[])
    assert p.has("app") is True
    assert p.has("hub") is True
    assert p.has("connector:read") is False and p.has("admin") is False
    assert p.is_connector is False


# ── registration / authorize audit logging ───────────────────────────

def test_register_audit_logs_allowed_and_blocked(gw, monkeypatch, caplog):
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOWED_REDIRECTS", _CLAUDE)
    _config, _db, _oauth = gw
    _config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client, \
            caplog.at_level(logging.INFO, logger="cortex_gateway.oauth"):
        client.post("/oauth/register",
                    json={"client_name": "claude", "redirect_uris": [_CLAUDE]})
        client.post("/oauth/register",
                    json={"client_name": "evil",
                          "redirect_uris": ["https://attacker.example/cb"]})
    text = caplog.text
    # Successful registration: INFO audit with outcome + redirect + client name.
    assert '"event":"register","outcome":"allowed"' in text
    assert _CLAUDE in text and '"client_name":"claude"' in text
    # Blocked registration: audit with reason + the attempted (untrusted) URI.
    assert '"outcome":"blocked"' in text
    assert '"reason":"redirect_not_allowlisted"' in text
    assert "https://attacker.example/cb" in text
    # The block is logged at WARNING (easy to filter).
    assert any(r.levelno == logging.WARNING and "blocked" in r.getMessage()
               for r in caplog.records)
    _config.get_settings.cache_clear()


def test_authorize_audit_records_requested_vs_granted_scope(gw, monkeypatch, caplog):
    # A scope-escalation attempt must stay visible in the log even though it is
    # stripped. In the GET-render flow this is logged when consent is shown.
    _allow_write(monkeypatch)   # so the granted scope keeps write (admin stripped)
    _config, db, _oauth = gw
    db.insert("oauth_clients", {
        "client_id": "cli_a", "client_name": "c",
        "redirect_uris": "https://claude.ai/api/mcp/auth_callback"})
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client, \
            caplog.at_level(logging.INFO, logger="cortex_gateway.oauth"):
        client.get("/oauth/authorize", params={
            "client_id": "cli_a",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": "x", "scope": "connector:write,admin"},
            headers={"x-ms-client-principal": "e30="})
    text = caplog.text
    assert '"event":"authorize","outcome":"prompt"' in text
    assert '"requested_scope":"connector:write,admin"' in text   # escalation attempt visible
    assert '"granted_scope":"connector:write"' in text           # admin stripped


def test_consent_approval_is_a_get_with_single_use_nonce(gw):
    # The Approve step must be a GET carrying a single-use consent nonce (Easy
    # Auth 403s the authenticated POST in the connector popup), and the redeemed
    # code must bind the nonce's stored params.
    _config, db, _oauth = gw
    db.insert("oauth_clients", {
        "client_id": "cli_b", "client_name": "grok",
        "redirect_uris": "https://grok.com/connectors-oauth-exchange-code/"})
    from starlette.testclient import TestClient
    human = {"x-ms-client-principal": "e30="}
    with TestClient(_oauth_app()) as client:
        # Render consent -> the page carries an Approve link with the nonce.
        r = client.get("/oauth/authorize", params={
            "client_id": "cli_b",
            "redirect_uri": "https://grok.com/connectors-oauth-exchange-code/",
            "code_challenge": "chal", "scope": "connector:read connector:write",
            "state": "xyz/1"}, headers=human)
        assert r.status_code == 200
        assert 'href="/oauth/authorize?consent=cnf_' in r.text
        nonce = r.text.split("consent=")[1].split('"')[0]
        # Approve via GET -> 302 with code + iss + preserved state.
        r = client.get(f"/oauth/authorize?consent={nonce}",
                       headers=human, follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://grok.com/connectors-oauth-exchange-code/?code=code_")
        assert "iss=" in loc and "state=xyz%2F1" in loc
        # Replaying the same nonce is rejected (single-use).
        assert client.get(f"/oauth/authorize?consent={nonce}",
                          headers=human, follow_redirects=False).status_code == 400


# ── discovery / rate-limit (Grok OAuth bootstrap fix) ─────────────────

def test_discovery_not_rate_limited():
    # A connector's OAuth bootstrap fetches the discovery docs in a burst far
    # exceeding the anon bucket; they must never 429 (regression: they did, and
    # Grok's connection failed).
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway import oauth
    from cortex_gateway.app import RateLimitMiddleware
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.include_router(oauth.router)
    with TestClient(app) as client:
        codes = [client.get("/.well-known/oauth-protected-resource").status_code
                 for _ in range(40)]
    assert all(c == 200 for c in codes), codes


def test_resource_metadata_served_at_suffixed_path():
    # RFC 9728 path-suffixed form that MCP clients (Grok) derive + probe.
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway import oauth
    app = FastAPI()
    app.include_router(oauth.router)
    with TestClient(app) as client:
        bare = client.get("/.well-known/oauth-protected-resource")
        suffixed = client.get("/.well-known/oauth-protected-resource/mcp")
    assert bare.status_code == 200 and suffixed.status_code == 200
    assert bare.json() == suffixed.json()
    assert bare.json()["resource"].endswith("/mcp")


def test_client_key_uses_forwarded_ip_for_anon():
    # Behind Azure the socket peer is the platform proxy; the anon bucket must
    # key on the forwarded client IP so callers don't share one global bucket.
    from starlette.requests import Request
    from cortex_gateway.app import _client_key
    req = Request({"type": "http", "method": "GET", "path": "/mcp/",
                   "query_string": b"", "client": ("169.254.130.2", 5),
                   "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")]})
    key, authed = _client_key(req)
    assert authed is False and key == "ip:203.0.113.7"


def test_ratelimiter_still_throttles_non_exempt_burst():
    # Guard: exempting discovery must not have disabled limiting elsewhere.
    from cortex_gateway.ratelimit import RateLimiter
    rl = RateLimiter(anon_rpm=1)
    results = [rl.check("ip:1.2.3.4", authenticated=False)[0] for _ in range(30)]
    assert results[0] is True and results[-1] is False


def test_login_redirect_preserves_query():
    # Regression: the Entra login round-trip must carry the OAuth query params
    # (client_id/redirect_uri/code_challenge) through, or /oauth/authorize 422s
    # on return from login (observed live breaking Grok).
    from urllib.parse import unquote
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway.app import HumanLoginMiddleware
    app = FastAPI()
    app.add_middleware(HumanLoginMiddleware)

    @app.get("/oauth/authorize")
    def _authorize():
        return {"ok": True}

    with TestClient(app) as client:
        r = client.get(
            "/oauth/authorize?response_type=code&client_id=cli_x"
            "&redirect_uri=https://grok.com/cb&code_challenge=abc123",
            follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/.auth/login/aad?post_login_redirect_uri=")
    decoded = unquote(loc)
    assert "client_id=cli_x" in decoded
    assert "redirect_uri=https://grok.com/cb" in decoded
    assert "code_challenge=abc123" in decoded


# ── debug tracing (GATEWAY_DEBUG) ─────────────────────────────────────

def _principal(**claims):
    import base64
    doc = {"auth_typ": "aad",
           "claims": [{"typ": k, "val": v} for k, v in claims.items()]}
    return base64.b64encode(__import__("json").dumps(doc).encode()).decode()


def test_decode_easyauth_identity():
    from cortex_gateway.app import _decode_easyauth_identity
    hdr = _principal(**{
        "name": "Ada Lovelace",
        "preferred_username": "ada@example.com",
        "http://schemas.microsoft.com/identity/claims/tenantid": "test-tenant"})
    out = _decode_easyauth_identity(hdr)
    assert out["idp"] == "aad"
    assert out["name"] == "Ada Lovelace"
    assert out["upn"] == "ada@example.com"
    assert out["tid"] == "test-tenant"


def test_decode_easyauth_identity_bad_input():
    from cortex_gateway.app import _decode_easyauth_identity
    assert _decode_easyauth_identity("!!not base64!!") == {"decode": "failed"}


def test_debug_trace_logs_identity_when_enabled(monkeypatch, caplog):
    monkeypatch.setenv("GATEWAY_DEBUG", "1")
    from cortex_gateway import config
    config.get_settings.cache_clear()
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway.app import DebugTraceMiddleware
    app = FastAPI()
    app.add_middleware(DebugTraceMiddleware)

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    hdr = _principal(preferred_username="ada@example.com")
    with TestClient(app) as client, \
            caplog.at_level(logging.INFO, logger="cortex_gateway.trace"):
        client.get("/ping?x=1", headers={"x-ms-client-principal": hdr})
    assert "oauth_trace" in caplog.text
    assert "ada@example.com" in caplog.text     # which identity Easy Auth injected
    assert '"authed":true' in caplog.text
    config.get_settings.cache_clear()


def test_debug_trace_silent_when_disabled(monkeypatch, caplog):
    monkeypatch.delenv("GATEWAY_DEBUG", raising=False)
    from cortex_gateway import config
    config.get_settings.cache_clear()
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway.app import DebugTraceMiddleware
    app = FastAPI()
    app.add_middleware(DebugTraceMiddleware)

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    with TestClient(app) as client, \
            caplog.at_level(logging.INFO, logger="cortex_gateway.trace"):
        client.get("/ping")
    assert "oauth_trace" not in caplog.text
    config.get_settings.cache_clear()


# ── atomic single-use codes (audit finding 11: replay race) ───────────

def test_code_consumption_is_atomic(gw):
    _config, db, _oauth = gw
    db.insert("oauth_codes", {
        "code": "code_test", "client_id": "cli_x",
        "redirect_uri": "https://claude.ai/cb",
        "code_challenge": "x", "scope": "connector:read",
        "expires_at": 9_999_999_999.0,
    })
    consume = ("UPDATE oauth_codes SET used = 1 "
               "WHERE code = :code AND used = 0")
    # First exchange wins, second (replay) is a no-op.
    assert db.execute_write(consume, {"code": "code_test"}) == 1
    assert db.execute_write(consume, {"code": "code_test"}) == 0


# ── expiry storage is dialect-portable ────────────────────────────────

def test_mint_accepts_iso_string_expiry(gw):
    # Regression: SQLAlchemy's SQLite dialect rejects strings on DateTime
    # columns, so mint() must coerce ISO strings (admin REST / CLI path).
    _config, db, _oauth = gw
    from cortex_gateway import auth
    raw = auth.mint(name="ttl-str", scopes="connector:read", kind="oauth",
                    expires_at="2099-08-10T12:00:00+00:00")
    assert auth.principal_from_bearer(f"Bearer {raw}").name == "ttl-str"


def test_expired_token_rejected(gw):
    _config, _db, _oauth = gw
    from fastapi import HTTPException
    from cortex_gateway import auth
    raw = auth.mint(name="ttl-past", scopes="connector:read", kind="oauth",
                    expires_at="2020-01-01T00:00:00+00:00")
    with pytest.raises(HTTPException):
        auth.principal_from_bearer(f"Bearer {raw}")


# ── end-to-end flow: register -> consent -> code -> token ────────────

def test_full_flow_end_to_end(gw, monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_TOKEN_TTL", "3600")
    monkeypatch.setenv("GATEWAY_OAUTH_ALLOW_WRITE", "1")   # exercise the write path
    _config, db, _oauth = gw
    _config.get_settings.cache_clear()

    from starlette.testclient import TestClient
    from cortex_gateway import auth

    verifier = "v" * 43
    with TestClient(_oauth_app()) as client:
        # 1. Dynamic registration (https redirect required).
        r = client.post("/oauth/register", json={
            "client_name": "claude",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})
        assert r.status_code == 201
        client_id = r.json()["client_id"]

        # 2. Consent is Entra-gated: anonymous GET redirects to platform login.
        r = client.get("/oauth/authorize", params={
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": _challenge(verifier)},
            follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("/.auth/")

        # 3a. Render consent with a (spoof-proof, Easy-Auth-injected) principal
        #     header + a scope-escalation attempt. Consent screen carries the
        #     Approve link with a single-use nonce.
        human = {"x-ms-client-principal": "e30="}
        r = client.get("/oauth/authorize", params={
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": _challenge(verifier),
            "scope": "connector:read connector:write,admin", "state": "xyz/1"},
            headers=human)
        assert r.status_code == 200
        nonce = r.text.split("consent=")[1].split('"')[0]

        # 3b. Approve via GET (not POST - Easy Auth blocks the authenticated POST).
        r = client.get(f"/oauth/authorize?consent={nonce}",
                       headers=human, follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://claude.ai/api/mcp/auth_callback?")
        assert "iss=" in loc and "state=xyz%2F1" in loc      # RFC 9207 + encoding
        code = loc.split("code=")[1].split("&")[0]

        # 4. Exchange with the right verifier; wrong verifier must fail first.
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": client_id, "code_verifier": "w" * 43}
        assert client.post("/oauth/token", data=form).status_code == 400
        form["code_verifier"] = verifier
        r = client.post("/oauth/token", data=form)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scope"] == "connector:read connector:write"  # admin stripped, RFC space form
        assert body["expires_in"] == 3600                    # finite TTL
        assert body["token_type"] == "Bearer"

        # 5. Replay of the consumed code is rejected.
        assert client.post("/oauth/token", data=form).status_code == 400

        # 6. The minted token verifies, resolves BOTH scopes, internal-capped.
        p = auth.principal_from_bearer("Bearer " + body["access_token"])
        assert p.is_connector and p.max_tier == "internal"
        assert p.has("connector:read") and p.has("connector:write")
        assert not p.has("admin") and not p.has("app")
        row = db.fetchone("SELECT expires_at FROM gateway_tokens "
                          "WHERE name = :n", {"n": f"oauth:{client_id}"})
        assert row["expires_at"]                              # expiry persisted

        # 7. The successful connection is recorded into Cortex (not just Azure).
        conn = db.fetchone("SELECT name, scope, max_tier FROM connector_connections "
                           "WHERE client_id = :c", {"c": client_id})
        assert conn and conn["name"] == "claude"
        assert conn["scope"] == "connector:read connector:write"
        assert conn["max_tier"] == "internal"


# ── finite access-token TTL ───────────────────────────────────────────

def test_oauth_token_ttl_default_is_finite(monkeypatch):
    monkeypatch.delenv("GATEWAY_OAUTH_TOKEN_TTL", raising=False)
    from cortex_gateway import config
    config.get_settings.cache_clear()
    ttl = config.get_settings().oauth_token_ttl
    assert 0 < ttl <= 24 * 3600            # finite and genuinely short-lived


def test_oauth_token_ttl_env_override(monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_TOKEN_TTL", "3600")
    from cortex_gateway import config
    config.get_settings.cache_clear()
    assert config.get_settings().oauth_token_ttl == 3600
