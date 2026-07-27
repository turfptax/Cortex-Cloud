"""OPT-8 identity binding: owner-assigned agent handles at approval.

The comms skeleton's blocker fix (OPT_PLAN 7.2): loopback connectors
never auto-bind a relay identity; the OWNER assigns a handle when
approving the connection. The handle lands on the grant row (gateway)
and, in the cloud ATTACH topology, on a corpus agents row through the
core's dedicated agent_assign command. These tests cover the /api web
surface, validation, the routed corpus write, and failure atomicity
(a failed corpus write aborts the whole approval).
"""
import base64
import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cortex_gateway.core_client import CoreWriteError

OWNER = "11111111-1111-1111-1111-111111111111"


def _principal(oid: str) -> dict:
    claims = [{"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
               "val": oid}]
    blob = base64.b64encode(
        json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()
    return {"x-ms-client-principal": blob}


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


def _seed_pending(client_id="cli_claude", name="Claude", host="claude.ai"):
    from cortex_gateway import grants
    grants.upsert_on_connect(client_id, name, host)
    return grants.grant_for(client_id)["id"]


def test_approve_with_handle_stores_and_lists(web):
    client, _db = web
    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "always": True,
                          "agent_handle": "claude-code"})
    assert r.status_code == 200
    assert r.json()["agent_handle"] == "claude-code"
    assert r.json()["status"] == "active"

    r = client.get("/api/connections", headers=OWNER_HDR)
    row = next(c for c in r.json()["connections"] if c["id"] == gid)
    assert row["agent_handle"] == "claude-code"


def test_approve_without_handle_unchanged(web):
    client, _db = web
    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full"})
    assert r.status_code == 200
    assert r.json()["agent_handle"] is None
    assert r.json()["status"] == "active"


@pytest.mark.parametrize("bad", ["Bad Handle!", "a", "-nope", "x" * 33])
def test_invalid_handle_400_and_grant_stays_pending(web, bad):
    client, _db = web
    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "agent_handle": bad})
    assert r.status_code == 400
    from cortex_gateway import grants
    g = grants.get_by_id(gid)
    assert g["status"] == "pending" and g["agent_handle"] is None


@pytest.mark.parametrize("reserved", ["tory", "overseer", "desktop-agent",
                                      "  TORY  "])
def test_reserved_handle_rejected(web, reserved):
    client, _db = web
    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "agent_handle": reserved})
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"]


def test_handle_normalized_lowercase(web):
    client, _db = web
    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "agent_handle": "  My-Agent2  "})
    assert r.status_code == 200
    assert r.json()["agent_handle"] == "my-agent2"


def test_routed_mode_writes_corpus_agents_row(web, monkeypatch):
    client, _db = web
    from cortex_gateway import grants
    calls = []
    monkeypatch.setattr(grants.corpus_writes, "routed", lambda: True)
    monkeypatch.setattr(grants.corpus_writes, "assign_agent",
                        lambda payload: calls.append(payload) or dict(payload))

    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "agent_handle": "claude-web"})
    assert r.status_code == 200
    assert len(calls) == 1
    p = calls[0]
    assert p["handle"] == "claude-web"
    assert p["kind"] == "connector"          # claude.ai is not a loopback host
    assert p["bind_kind"] == "grant"
    assert p["bind_value"] == str(gid)
    assert p["display_name"] == "Claude"


def test_routed_mode_loopback_kind(web, monkeypatch):
    client, _db = web
    from cortex_gateway import grants
    calls = []
    monkeypatch.setattr(grants.corpus_writes, "routed", lambda: True)
    monkeypatch.setattr(grants.corpus_writes, "assign_agent",
                        lambda payload: calls.append(payload) or dict(payload))

    gid = _seed_pending(client_id="cli_local", name="Claude Code",
                        host="localhost")
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "agent_handle": "claude-code"})
    assert r.status_code == 200
    assert calls[0]["kind"] == "loopback"


def test_core_failure_aborts_approval(web, monkeypatch):
    client, _db = web
    from cortex_gateway import grants

    def boom(payload):
        raise CoreWriteError("core unreachable")

    monkeypatch.setattr(grants.corpus_writes, "routed", lambda: True)
    monkeypatch.setattr(grants.corpus_writes, "assign_agent", boom)

    gid = _seed_pending()
    r = client.post(f"/api/connections/{gid}/approve", headers=OWNER_HDR,
                    json={"level": "full", "agent_handle": "claude-web"})
    assert r.status_code == 502
    g = grants.get_by_id(gid)
    assert g["status"] == "pending" and g["agent_handle"] is None


def test_v1_approve_passes_handle_through(web):
    """The phone surface (/v1/connections) shares grants.approve; verify
    the ApproveIn model accepts agent_handle and it lands on the grant."""
    _client, _db = web
    from cortex_gateway import grants
    from cortex_gateway.rest.connections import ApproveIn
    gid = _seed_pending(client_id="cli_phone_path", name="Grok",
                        host="grok.com")
    body = ApproveIn(level="full", agent_handle="grok-agent")
    out = grants.approve(gid, body.level, always=body.always, by="app",
                         agent_handle=body.agent_handle or None)
    assert out["agent_handle"] == "grok-agent"
    assert out["status"] == "active"
