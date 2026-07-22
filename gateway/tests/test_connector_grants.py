"""Connector access grants - the DB model, the read gate, and the endpoints.
See docs/CONNECTOR_GRANTS_DESIGN.md.
"""
from cortex_gateway import corpus_service as cs
from cortex_gateway import grants
from cortex_gateway.auth import Principal


def _conn(client_id):
    return Principal(id=1, name=f"oauth:{client_id}", kind="oauth",
                     scopes={"connector:read"}, max_tier="internal",
                     category_filter=[], client_id=client_id)


def _register(db, client_id, redirect, name="c"):
    db.insert("oauth_clients", {"client_id": client_id, "client_name": name,
                                "redirect_uris": redirect})


# ── default deny + seeding ────────────────────────────────────────────

def test_new_connector_is_pending_and_reads_nothing(gw, monkeypatch):
    monkeypatch.delenv("GATEWAY_CONNECTOR_FULL_HOSTS", raising=False)
    _config, db, _ = gw
    _config.get_settings.cache_clear()
    _register(db, "cli_claude", "https://claude.ai/api/mcp/auth_callback")
    grants.upsert_on_connect("cli_claude", "claude", "claude.ai")
    g = grants.grant_for("cli_claude")
    assert g["status"] == "pending" and g["level"] == "none"
    p = _conn("cli_claude")
    assert grants.has_full_access(p) is False
    assert cs._gate_decision(p, "summaries_gist", {"body": "x"}) == "withheld"
    _config.get_settings.cache_clear()


def test_seed_full_host_is_grandfathered(gw, monkeypatch):
    monkeypatch.setenv("GATEWAY_CONNECTOR_FULL_HOSTS", "grok.com")
    _config, db, _ = gw
    _config.get_settings.cache_clear()
    _register(db, "cli_grok", "https://grok.com/connectors-oauth-exchange-code/")
    grants.upsert_on_connect("cli_grok", "grok", "grok.com")
    g = grants.grant_for("cli_grok")
    assert (g["status"], g["level"], g["approval_policy"]) == ("active", "full", "always")
    p = _conn("cli_grok")
    assert grants.has_full_access(p) is True
    assert cs._gate_decision(p, "summaries_gist", {"body": "x"}) == "full"
    _config.get_settings.cache_clear()


def test_app_token_unaffected(gw):
    _config, db, _ = gw
    app = Principal(id=2, name="phone", kind="app", scopes={"app"},
                    max_tier="restricted", category_filter=[])
    assert grants.has_full_access(app) is True
    assert cs._gate_decision(app, "summaries_gist", {"body": "x"}) == "full"


# ── approve / revoke are immediate ────────────────────────────────────

def test_approve_then_revoke_take_effect_immediately(gw, monkeypatch):
    monkeypatch.delenv("GATEWAY_CONNECTOR_FULL_HOSTS", raising=False)
    _config, db, _ = gw
    _config.get_settings.cache_clear()
    _register(db, "cli_x", "https://x.example/cb")
    grants.upsert_on_connect("cli_x", "x", "x.example")
    gid = grants.grant_for("cli_x")["id"]
    p = _conn("cli_x")
    assert cs._gate_decision(p, "summaries_gist", {"body": "x"}) == "withheld"   # pending
    grants.approve(gid, "full")
    assert cs._gate_decision(p, "summaries_gist", {"body": "x"}) == "full"       # immediate
    grants.revoke(gid)
    assert cs._gate_decision(p, "summaries_gist", {"body": "x"}) == "withheld"   # immediate
    assert grants.grant_for("cli_x")["status"] == "revoked"
    _config.get_settings.cache_clear()


# ── confirmation policy ───────────────────────────────────────────────

def test_ask_policy_reconfirms_on_reconnect(gw, monkeypatch):
    monkeypatch.delenv("GATEWAY_CONNECTOR_FULL_HOSTS", raising=False)
    _config, db, _ = gw
    _config.get_settings.cache_clear()
    _register(db, "cli_ask", "https://ask.example/cb")
    grants.upsert_on_connect("cli_ask", "ask", "ask.example")   # pending / ask
    gid = grants.grant_for("cli_ask")["id"]
    grants.approve(gid, "full")                                 # active, still 'ask'
    assert grants.grant_for("cli_ask")["status"] == "active"
    grants.upsert_on_connect("cli_ask", "ask", "ask.example")   # a new connection
    assert grants.grant_for("cli_ask")["status"] == "pending"   # must re-confirm
    _config.get_settings.cache_clear()


def test_always_policy_stays_active_on_reconnect(gw, monkeypatch):
    monkeypatch.delenv("GATEWAY_CONNECTOR_FULL_HOSTS", raising=False)
    _config, db, _ = gw
    _config.get_settings.cache_clear()
    _register(db, "cli_a", "https://a.example/cb")
    grants.upsert_on_connect("cli_a", "a", "a.example")
    gid = grants.grant_for("cli_a")["id"]
    grants.approve(gid, "full", always=True)                    # active + always
    grants.upsert_on_connect("cli_a", "a", "a.example")         # reconnect
    assert grants.grant_for("cli_a")["status"] == "active"      # not reset
    _config.get_settings.cache_clear()


# ── endpoints ─────────────────────────────────────────────────────────

def test_connections_endpoints(gw, monkeypatch):
    monkeypatch.delenv("GATEWAY_CONNECTOR_FULL_HOSTS", raising=False)
    _config, db, _ = gw
    _config.get_settings.cache_clear()
    from cortex_gateway import auth
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway.rest import connections as rc

    _register(db, "cli_e", "https://e.example/cb")
    grants.upsert_on_connect("cli_e", "grok", "e.example")
    gid = grants.grant_for("cli_e")["id"]
    app_tok = auth.mint("phone", "app", kind="app")
    conn_tok = auth.mint("grok", "connector:read", kind="connector")

    app = FastAPI()
    app.include_router(rc.router)
    with TestClient(app) as c:
        H = {"Authorization": f"Bearer {app_tok}"}
        r = c.get("/v1/connections", headers=H)
        assert r.status_code == 200
        assert any(x["id"] == gid for x in r.json()["connections"])
        # a connector token can NEVER manage connections (no app scope).
        assert c.get("/v1/connections",
                     headers={"Authorization": f"Bearer {conn_tok}"}).status_code == 403
        # approve with always
        r = c.post(f"/v1/connections/{gid}/approve",
                   json={"level": "full", "always": True}, headers=H)
        assert r.status_code == 200
        assert (r.json()["status"], r.json()["level"],
                r.json()["approval_policy"]) == ("active", "full", "always")
        # invalid level rejected
        assert c.post(f"/v1/connections/{gid}/approve",
                      json={"level": "bogus"}, headers=H).status_code == 400
        # policy change
        assert c.post(f"/v1/connections/{gid}/policy",
                      json={"approval_policy": "ask"}, headers=H).json()["approval_policy"] == "ask"
        # revoke
        r = c.post(f"/v1/connections/{gid}/revoke", headers=H)
        assert r.status_code == 200 and r.json()["status"] == "revoked"
        # unknown id -> 404
        assert c.post("/v1/connections/99999/revoke", headers=H).status_code == 404
    _config.get_settings.cache_clear()
