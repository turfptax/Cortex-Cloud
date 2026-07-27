"""OAuth refresh tokens + the durable auth-failure log.

Why these exist: access tokens carry a finite TTL, and with no refresh grant the
only way to renew was to re-run the browser consent flow by hand. Connectors
cannot do that on their own, so every expiry read to the owner as "the connector
broke again". These tests pin the renewal path AND the security properties that
make a long-lived refresh credential safe:

  - rotation on every use, successor stays in the same family
  - reuse of a spent token burns the whole chain (OAuth 2.1 BCP)
  - a revoked connection can NEVER refresh its way back to life
"""
import base64
import hashlib

import pytest

_REDIRECT = "https://claude.ai/api/mcp/auth_callback"
_HUMAN = {"x-ms-client-principal": "e30="}


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _oauth_app():
    from fastapi import FastAPI
    from cortex_gateway import oauth
    from cortex_gateway.app import HumanLoginMiddleware
    app = FastAPI()
    app.add_middleware(HumanLoginMiddleware)
    app.include_router(oauth.router)
    return app


def _connect(client, name="claude", redirect=_REDIRECT):
    """Register -> consent -> approve -> exchange. Returns (client_id, body)."""
    verifier = "v" * 43
    r = client.post("/oauth/register",
                    json={"client_name": name, "redirect_uris": [redirect]})
    assert r.status_code == 201, r.text
    client_id = r.json()["client_id"]
    r = client.get("/oauth/authorize", params={
        "client_id": client_id, "redirect_uri": redirect,
        "code_challenge": _challenge(verifier)}, headers=_HUMAN)
    assert r.status_code == 200, r.text
    nonce = r.text.split("consent=")[1].split('"')[0]
    r = client.get(f"/oauth/authorize?consent={nonce}", headers=_HUMAN,
                   follow_redirects=False)
    assert r.status_code == 302
    code = r.headers["location"].split("code=")[1].split("&")[0]
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect, "client_id": client_id,
        "code_verifier": verifier})
    assert r.status_code == 200, r.text
    return client_id, r.json()


@pytest.fixture()
def oauth_client(gw, monkeypatch):
    monkeypatch.setenv("GATEWAY_OAUTH_TOKEN_TTL", "3600")
    config, _db, _oauth = gw
    config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        yield client


# ── issuance ──────────────────────────────────────────────────────────

def test_authorization_code_issues_a_refresh_token(oauth_client):
    _cid, body = _connect(oauth_client)
    assert body["refresh_token"].startswith("rft_")
    assert body["expires_in"] == 3600


def test_no_refresh_token_when_access_token_never_expires(gw, monkeypatch):
    # Nothing to refresh if the access token is immortal, so issuing a
    # long-lived refresh credential would be pure extra attack surface.
    monkeypatch.setenv("GATEWAY_OAUTH_TOKEN_TTL", "0")
    config, _db, _oauth = gw
    config.get_settings.cache_clear()
    from starlette.testclient import TestClient
    with TestClient(_oauth_app()) as client:
        _cid, body = _connect(client)
    assert "refresh_token" not in body
    assert "expires_in" not in body


def test_only_the_hash_of_a_refresh_token_is_stored(oauth_client, gw):
    _config, db, _oauth = gw
    _cid, body = _connect(oauth_client)
    raw = body["refresh_token"]
    rows = db.fetchall("SELECT token_hash FROM oauth_refresh_tokens")
    assert rows and all(r["token_hash"] != raw for r in rows)


# ── the renewal path (the actual fix) ─────────────────────────────────

def test_refresh_grant_mints_a_working_access_token(oauth_client):
    from cortex_gateway import auth
    cid, body = _connect(oauth_client)
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"],
        "client_id": cid})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"] != body["access_token"]
    assert new["refresh_token"] != body["refresh_token"]     # rotated
    assert new["scope"] == body["scope"]
    p = auth.principal_from_bearer("Bearer " + new["access_token"])
    assert p.is_connector and p.has("connector:read")
    assert p.client_id == cid


def test_refresh_works_without_client_id(oauth_client):
    # A public client may omit client_id on refresh; it must still work.
    _cid, body = _connect(oauth_client)
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"]})
    assert r.status_code == 200, r.text


def test_refresh_form_needs_no_code_fields(oauth_client):
    # Regression: code/redirect_uri/code_verifier were REQUIRED form fields, so
    # a refresh request 422'd before the grant_type branch could ever run.
    _cid, body = _connect(oauth_client)
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"]})
    assert r.status_code != 422


def test_refresh_chain_survives_repeated_rotation(oauth_client):
    cid, body = _connect(oauth_client)
    token = body["refresh_token"]
    for _ in range(5):
        r = oauth_client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": token,
            "client_id": cid})
        assert r.status_code == 200, r.text
        token = r.json()["refresh_token"]


def test_refresh_does_not_reset_an_approved_grant_to_pending(oauth_client, gw):
    # upsert_on_connect knocks an `ask` grant back to pending. If refresh called
    # it, every rotation would drop the connector into the approval queue.
    _config, db, _oauth = gw
    from cortex_gateway import grants
    cid, body = _connect(oauth_client)
    grants.approve(grants.grant_for(cid)["id"], "full")
    before = db.fetchone("SELECT status, level FROM connector_grants "
                         "WHERE client_id = :c", {"c": cid})
    assert before["status"] == "active"
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"],
        "client_id": cid})
    assert r.status_code == 200, r.text
    after = db.fetchone("SELECT status, level FROM connector_grants "
                        "WHERE client_id = :c", {"c": cid})
    assert after["status"] == "active" and after["level"] == before["level"]


# ── rotation security: reuse detection ────────────────────────────────

def test_reusing_a_spent_refresh_token_burns_the_whole_family(oauth_client, gw):
    _config, db, _oauth = gw
    from cortex_gateway import auth
    cid, body = _connect(oauth_client)
    first = body["refresh_token"]
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first,
        "client_id": cid})
    assert r.status_code == 200
    second = r.json()["refresh_token"]

    # Replaying the spent token means two parties hold it: burn the chain.
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first,
        "client_id": cid})
    assert r.status_code == 400
    assert "reuse" in r.text

    # The successor is dead too, so the thief's copy is worthless...
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": second,
        "client_id": cid})
    assert r.status_code == 400

    # ...and every access token for the connection is revoked.
    live = db.fetchall("SELECT id FROM gateway_tokens WHERE client_id = :c "
                       "AND revoked_at IS NULL", {"c": cid})
    assert live == []
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        auth.principal_from_bearer("Bearer " + r.json().get("access_token", "x"))


def test_unknown_and_garbage_refresh_tokens_are_rejected(oauth_client):
    for bad in ("rft_nope", "", "   ", "Bearer rft_x"):
        r = oauth_client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": bad})
        assert r.status_code == 400


def test_expired_refresh_token_is_rejected(gw, oauth_client):
    _config, db, _oauth = gw
    _cid, body = _connect(oauth_client)
    db.execute("UPDATE oauth_refresh_tokens SET expires_at = "
               "'2020-01-01 00:00:00'")
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"]})
    assert r.status_code == 400 and "expired" in r.text


def test_refresh_token_is_bound_to_its_client(oauth_client):
    _cid, body = _connect(oauth_client)
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"],
        "client_id": "cli_someone_else"})
    assert r.status_code == 400 and "mismatch" in r.text


def test_unsupported_grant_type_rejected(oauth_client):
    r = oauth_client.post("/oauth/token", data={"grant_type": "password"})
    assert r.status_code == 400 and "unsupported_grant_type" in r.text


def test_authorization_code_grant_still_validates_its_fields(oauth_client):
    # Loosening the form signature must not let a code exchange through
    # without a verifier.
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": "code_x"})
    assert r.status_code == 400 and "invalid_request" in r.text


# ── revocation must be final ──────────────────────────────────────────

def test_revoked_connection_cannot_refresh_back_to_life(oauth_client, gw):
    _config, _db, _oauth = gw
    from cortex_gateway import grants
    cid, body = _connect(oauth_client)
    grants.revoke(grants.grant_for(cid)["id"])
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"],
        "client_id": cid})
    assert r.status_code == 400, "a disconnected connector refreshed itself back"


def test_grant_revoke_reports_refresh_tokens_revoked(oauth_client, gw):
    _config, _db, _oauth = gw
    from cortex_gateway import grants
    cid, _body = _connect(oauth_client)
    out = grants.revoke(grants.grant_for(cid)["id"])
    assert out["refresh_tokens_revoked"] >= 1


def test_startup_dedupe_kills_the_refresh_chain(oauth_client, gw):
    # Two registrations of the same service collapse to one; the loser's
    # refresh chain must die with its access tokens.
    _config, db, _oauth = gw
    from cortex_gateway import grants
    cid_a, body_a = _connect(oauth_client, name="dupe")
    db.execute("UPDATE oauth_clients SET client_id = :new WHERE client_id = :old",
               {"new": cid_a + "_x", "old": cid_a})
    cid_b, _body_b = _connect(oauth_client, name="dupe")
    assert cid_b != cid_a
    grants.dedupe_connections()
    r = oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": body_a["refresh_token"]})
    assert r.status_code == 400


def test_revoke_for_client_is_idempotent(gw):
    _config, _db, _oauth = gw
    from cortex_gateway import refresh
    refresh.issue(client_id="cli_z", name="oauth:cli_z", kind="oauth",
                  scope="connector:read", max_tier="internal", ttl=60)
    assert refresh.revoke_for_client("cli_z") == 1
    assert refresh.revoke_for_client("cli_z") == 0
    assert refresh.revoke_for_client("") == 0


# ── durable auth-failure log ──────────────────────────────────────────

def test_auth_failure_is_recorded_with_a_non_secret_prefix(gw):
    _config, db, _oauth = gw
    from cortex_gateway import authlog
    authlog.record("invalid or revoked token", path="/mcp/",
                   authorization="Bearer ctx_supersecrettokenvalue",
                   source_ip="203.0.113.7", user_agent="claude")
    row = db.fetchone("SELECT * FROM auth_failures ORDER BY id DESC")
    assert row["reason"] == "invalid or revoked token"
    assert row["path"] == "/mcp/" and row["source_ip"] == "203.0.113.7"
    # A correlation hint, never the secret itself.
    assert row["key_prefix"] == "ctx_supersec"
    assert "supersecrettokenvalue" not in str(row["key_prefix"])


def test_auth_failure_logging_never_raises(gw, monkeypatch):
    # A logging problem must never turn a 401 into a 500.
    _config, db, _oauth = gw
    from cortex_gateway import authlog
    monkeypatch.setattr(db, "insert", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("disk gone")))
    authlog.record("boom", path="/mcp/")          # must not raise


def test_auth_failure_handles_missing_bearer(gw):
    _config, db, _oauth = gw
    from cortex_gateway import authlog
    authlog.record("missing bearer token", path="/mcp/", authorization=None)
    row = db.fetchone("SELECT key_prefix FROM auth_failures ORDER BY id DESC")
    assert row["key_prefix"] is None


def test_token_endpoint_failures_reach_the_log(oauth_client, gw):
    _config, db, _oauth = gw
    oauth_client.post("/oauth/token", data={"grant_type": "password"})
    row = db.fetchone("SELECT reason, path FROM auth_failures ORDER BY id DESC")
    assert row["reason"] == "unsupported_grant_type"
    assert row["path"] == "/oauth/token"


def test_refresh_reuse_is_recorded_as_a_failure(oauth_client, gw):
    _config, db, _oauth = gw
    cid, body = _connect(oauth_client)
    first = body["refresh_token"]
    oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first, "client_id": cid})
    oauth_client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first, "client_id": cid})
    row = db.fetchone("SELECT reason FROM auth_failures ORDER BY id DESC")
    assert row["reason"] == "refresh_token_reuse"


def test_existing_database_gains_both_new_tables(tmp_path, monkeypatch):
    """Replay against the OLD shape: the live gateway.db predates these tables,
    so boot must add them without disturbing what is already there."""
    import importlib
    import sqlite3
    db_file = tmp_path / "old_gw.db"
    old = sqlite3.connect(db_file)
    old.executescript("""
        CREATE TABLE gateway_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(200) NOT NULL,
            kind VARCHAR(20) NOT NULL DEFAULT 'connector',
            token_hash VARCHAR(64) NOT NULL UNIQUE, key_prefix VARCHAR(16),
            scopes VARCHAR(200) NOT NULL DEFAULT 'connector:read',
            max_tier VARCHAR(20) NOT NULL DEFAULT 'internal',
            category_filter VARCHAR(400), note VARCHAR(400),
            created_at DATETIME, last_used_at DATETIME, expires_at DATETIME,
            revoked_at DATETIME, client_id VARCHAR(80));
        CREATE TABLE oauth_clients (
            client_id VARCHAR(80) PRIMARY KEY, client_name VARCHAR(200),
            redirect_uris TEXT NOT NULL, created_at DATETIME);
    """)
    old.execute("INSERT INTO gateway_tokens (name, token_hash, scopes) "
                "VALUES ('pre-existing', 'deadbeef', 'connector:read')")
    old.execute("INSERT INTO oauth_clients (client_id, client_name, "
                "redirect_uris) VALUES ('cli_old', 'Claude', :r)",
                {"r": _REDIRECT})
    old.commit()
    old.close()

    monkeypatch.setenv("DB_URL", "sqlite:///" + str(db_file).replace("\\", "/"))
    monkeypatch.setenv("GATEWAY_OAUTH_ENABLED", "1")
    from cortex_gateway import config, db
    config.get_settings.cache_clear()
    db.engine.cache_clear()
    importlib.reload(db)
    db.init_schema()

    names = {r["name"] for r in db.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"oauth_refresh_tokens", "auth_failures"} <= names
    # Pre-existing rows untouched.
    assert db.fetchone("SELECT name FROM gateway_tokens")["name"] == "pre-existing"
    assert db.fetchone("SELECT client_name FROM oauth_clients")["client_name"] == "Claude"
    # And both new tables are immediately writable.
    from cortex_gateway import authlog, refresh
    raw = refresh.issue(client_id="cli_old", name="oauth:cli_old", kind="oauth",
                        scope="connector:read", max_tier="internal", ttl=60)
    assert refresh.redeem(raw)["client_id"] == "cli_old"
    authlog.record("replay-probe", path="/mcp/")
    assert db.fetchone("SELECT reason FROM auth_failures")["reason"] == "replay-probe"


def test_mcp_401_logs_only_when_a_token_was_presented(gw):
    """A presented-but-dead token is the signal worth keeping; anonymous probes
    are scanner noise that would bury it."""
    from starlette.testclient import TestClient
    from fastapi import FastAPI
    from cortex_gateway import auth, authlog, db
    from cortex_gateway.app import MCPBearerMiddleware
    authlog._schema_ready = False

    app = FastAPI()
    app.add_middleware(MCPBearerMiddleware, public_url="https://gw.example.com")

    @app.post("/mcp/")
    def _mcp():                                     # pragma: no cover
        return {"ok": True}

    dead = auth.mint(name="expired-conn", scopes="connector:read", kind="oauth",
                     expires_at="2020-01-01T00:00:00+00:00",
                     client_id="cli_dead")
    with TestClient(app) as client:
        assert client.post("/mcp/").status_code == 401          # anonymous
        assert client.post("/mcp/", headers={
            "Authorization": f"Bearer {dead}"}).status_code == 401

    rows = db.fetchall("SELECT reason, key_prefix FROM auth_failures")
    assert len(rows) == 1, "anonymous probe should not be logged"
    assert rows[0]["reason"] == "invalid or revoked token"
    assert rows[0]["key_prefix"] == dead[:12]


def test_authlog_recent_returns_newest_first(gw):
    _config, _db, _oauth = gw
    from cortex_gateway import authlog
    authlog.record("first", path="/mcp/")
    authlog.record("second", path="/mcp/")
    rows = authlog.recent(limit=2)
    assert [r["reason"] for r in rows] == ["second", "first"]
