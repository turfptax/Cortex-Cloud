"""Owner-facing connector approval on the /api web facade.

A friend deploying their own instance may have no phone app, so the owner
must be able to approve/revoke connector connections from the web (Entra
session), not only via the phone's /v1/connections (app-scope) surface.
These routes reuse the same grants.* logic, gated by the /api router's
owner-login + CSRF dependency.
"""
import base64
import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _principal(oid: str) -> dict:
    claims = [{"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
               "val": oid}]
    blob = base64.b64encode(
        json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()
    return {"x-ms-client-principal": blob}


# owner + same-origin (satisfies the CSRF guard on writes)
OWNER_HDR = {**_principal(OWNER), "origin": "https://hub.test"}


@pytest.fixture()
def web(tmp_path, monkeypatch):
    db_file = tmp_path / "gw.db"
    monkeypatch.setenv("DB_URL", "sqlite:///" + str(db_file).replace("\\", "/"))
    monkeypatch.setenv("GATEWAY_OWNER_OIDS", OWNER)
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "https://hub.test")
    monkeypatch.setenv("CORTEX_SERVICE_TOKEN", "tok")

    from cortex_gateway import config, db
    config.get_settings.cache_clear()
    db.engine.cache_clear()
    importlib.reload(db)
    db.init_schema()

    from cortex_gateway.rest import hub_api
    app = FastAPI()
    app.include_router(hub_api.router)
    client = TestClient(app, follow_redirects=False)
    yield client, db
    config.get_settings.cache_clear()


def _seed_pending(client_id="cli_claude"):
    from cortex_gateway import grants
    grants.upsert_on_connect(client_id, "Claude", "claude.ai")
    return grants.grant_for(client_id)["id"]


def test_owner_lists_and_approves(web):
    client, _db = web
    gid = _seed_pending()
    r = client.get("/api/connections", headers=OWNER_HDR)
    assert r.status_code == 200
    conns = r.json()["connections"]
    assert any(c["id"] == gid and c["status"] == "pending" for c in conns)

    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "always": True})
    assert r.status_code == 200
    assert r.json()["status"] == "active" and r.json()["level"] == "full"

    # and the corpus gate now grants full access to that connection
    from cortex_gateway import grants
    from cortex_gateway.auth import Principal
    p = Principal(id=1, name="oauth:cli_claude", kind="oauth",
                  scopes={"connector:read"}, max_tier="internal",
                  category_filter=[], client_id="cli_claude")
    assert grants.has_full_access(p) is True
    assert grants.can_write(p) is True


def test_owner_revokes(web):
    client, _db = web
    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/revoke", headers=OWNER_HDR)
    assert r.status_code == 200 and r.json()["ok"] is True
    from cortex_gateway import grants
    assert grants.grant_for("cli_claude")["status"] == "revoked"


def test_non_owner_is_forbidden(web):
    client, _db = web
    _seed_pending()
    r = client.get("/api/connections",
                   headers={**_principal(OTHER), "origin": "https://hub.test"})
    assert r.status_code == 403


def test_unauthenticated_is_401(web):
    client, _db = web
    assert client.get("/api/connections").status_code == 401


def test_approve_write_needs_same_origin_csrf(web):
    client, _db = web
    gid = _seed_pending()
    # owner principal but no matching Origin: the CSRF guard blocks the write
    r = client.post(f"/api/connections/{gid}/approve",
                    headers=_principal(OWNER), json={"level": "full"})
    assert r.status_code == 403
