"""The phone's reprocess-day button reaches the core with the right ask.

POST /v1/day/reprocess {day} must force a FRESH daily narrative for
exactly that day (force=True, label-derived bounds), carry the long
read timeout the LLM call needs, refuse anything that is not a date,
and surface a down core as 502 rather than a hang.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cortex_gateway.auth import Principal
from cortex_gateway.core_client import CoreWriteError


class _CoreRec:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def post(self, path, payload, read=None):
        self.calls.append((path, payload, read))
        if self.error:
            raise self.error
        return {"ok": True, "narrative": {"kind": "daily"}}


def _client(monkeypatch, rec):
    from cortex_gateway.rest import corpus
    app = FastAPI()
    app.include_router(corpus.router)
    stub = Principal(id=1, name="hub", kind="token", scopes={"app"},
                     max_tier="private", category_filter=[], client_id=None)
    app.dependency_overrides[corpus._app] = lambda: stub
    monkeypatch.setattr(corpus, "core", lambda: rec)
    return TestClient(app)


def test_reprocess_forces_a_fresh_daily(gw, monkeypatch):
    rec = _CoreRec()
    client = _client(monkeypatch, rec)
    r = client.post("/v1/day/reprocess", json={"day": "2026-08-08"})
    assert r.status_code == 200
    path, payload, read = rec.calls[0]
    assert path == "/plugins/overseer/temporal/generate"
    assert payload == {"kind": "daily", "period_label": "2026-08-08",
                       "force": True}
    assert read == 120.0   # the LLM call outlives the tight write default


def test_reprocess_rejects_non_dates(gw, monkeypatch):
    rec = _CoreRec()
    client = _client(monkeypatch, rec)
    for bad in ("tomorrow", "2026-8-9", "2026-08-09 or 1=1", ""):
        r = client.post("/v1/day/reprocess", json={"day": bad})
        assert r.status_code == 400, bad
    assert rec.calls == []


def test_reprocess_maps_core_failure_to_502(gw, monkeypatch):
    rec = _CoreRec(error=CoreWriteError("core unreachable"))
    client = _client(monkeypatch, rec)
    r = client.post("/v1/day/reprocess", json={"day": "2026-08-08"})
    assert r.status_code == 502
