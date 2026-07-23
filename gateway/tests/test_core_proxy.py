"""The /core proxy must forward the caller's headers to the co-located core.

Regression guard: the proxy previously sent only Content-Type, dropping
X-Filename / X-Description / X-Tags, so every desktop import failed at the
upload step with the core replying "Missing X-Filename header".
"""
import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _RecordingClient:
    """Stands in for httpx.AsyncClient; records the forwarded request."""
    last: dict = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kw):
        _RecordingClient.last = {"method": method, "url": url, **kw}

        class _Resp:
            content = b'{"ok": true}'
            status_code = 200
            headers = {"content-type": "application/json"}
        return _Resp()


@pytest.fixture()
def proxy(monkeypatch):
    monkeypatch.setenv("CORTEX_SERVICE_TOKEN", "svc-tok")
    monkeypatch.setenv("CORTEX_CORE_URL", "http://127.0.0.1:8420")
    from cortex_gateway import config
    config.get_settings.cache_clear()
    from cortex_gateway.rest import core_proxy
    monkeypatch.setattr(core_proxy.httpx, "AsyncClient", _RecordingClient)
    _RecordingClient.last = {}
    app = FastAPI()
    app.include_router(core_proxy.router)
    yield TestClient(app, follow_redirects=False)
    config.get_settings.cache_clear()


def _basic(pwd="svc-tok"):
    return {"Authorization": "Basic "
            + base64.b64encode(("cortex:" + pwd).encode()).decode()}


def test_forwards_custom_headers_to_core(proxy):
    r = proxy.post("/core/files/uploads",
                   headers={**_basic(), "X-Filename": "chat.jsonl",
                            "X-Tags": "claude-code"},
                   content=b"{}")
    assert r.status_code == 200
    fwd = {k.lower(): v for k, v in _RecordingClient.last["headers"].items()}
    assert fwd.get("x-filename") == "chat.jsonl"   # the core needs this
    assert fwd.get("x-tags") == "claude-code"
    # but NOT the caller's auth/host: the proxy re-authenticates to the core
    assert "authorization" not in fwd
    assert "host" not in fwd


def test_rejects_wrong_token(proxy):
    r = proxy.post("/core/api/cmd", headers=_basic("wrong"), content=b"{}")
    assert r.status_code == 401


def test_accepts_bearer_token(proxy):
    r = proxy.post("/core/api/cmd",
                   headers={"Authorization": "Bearer svc-tok"}, content=b"{}")
    assert r.status_code == 200
