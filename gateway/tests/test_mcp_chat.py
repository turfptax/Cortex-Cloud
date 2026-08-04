"""Talking to the overseer over MCP, without losing the reply to a timeout.

A full overseer reply takes 45-70s (measured 47s and 64s on 2026-08-04) and
MCP clients abandon the call around 60s with error -32001. The ceiling is the
caller's, so these tests cover the two escapes: progress notifications during
a synchronous call, and a job pattern that outlives the connection entirely.
"""
import asyncio

import httpx
import pytest

from cortex_gateway import mcp_server as ms
from cortex_gateway.auth import Principal


class _Ctx:
    """Stand-in for FastMCP's Context; records heartbeats."""

    def __init__(self):
        self.progress = []

    async def report_progress(self, progress, total=None, message=None):
        self.progress.append((progress, message))


class _DeafCtx(_Ctx):
    """A client that cannot receive progress at all (no progressToken)."""

    async def report_progress(self, *a, **k):
        raise RuntimeError("no progress token")


def _writer():
    return Principal(id=1, name="claude", kind="connector",
                     scopes={"connector:read", "connector:write"},
                     max_tier="internal", category_filter=[])


def _reader():
    return Principal(id=2, name="grok", kind="connector",
                     scopes={"connector:read"}, max_tier="internal",
                     category_filter=[])


@pytest.fixture()
def as_writer(monkeypatch):
    monkeypatch.setattr(ms, "_principal", _writer)
    monkeypatch.setattr(ms.grants, "can_write", lambda p: True)


@pytest.fixture()
def as_reader(monkeypatch):
    monkeypatch.setattr(ms, "_principal", _reader)
    monkeypatch.setattr(ms.grants, "can_write", lambda p: False)


def _stub_core(monkeypatch, handler, *, delay: float = 0.0):
    """Point the chat tools at a fake core."""
    async def _handler(request: httpx.Request) -> httpx.Response:
        if delay:
            await asyncio.sleep(delay)
        return handler(request)

    def _factory(read):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_handler),
            timeout=httpx.Timeout(connect=5.0, read=read, write=5.0,
                                  pool=5.0))
    monkeypatch.setattr(ms, "_core_client", _factory)


# ── the job pattern: the path that always works ──────────────────────

def test_chat_start_returns_a_job_id(gw, monkeypatch, as_writer):
    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "job_id": "abc123", "thread_id": 7,
                   "status": "running"}))

    res = asyncio.run(ms.cortex_chat_start("what did I work on?"))

    assert res["ok"] and res["job_id"] == "abc123"
    assert res["status"] == "running"


def test_chat_result_passes_through_running_then_done(gw, monkeypatch,
                                                      as_writer):
    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "job_id": "abc123", "status": "running",
                   "elapsed_s": 22}))
    running = asyncio.run(ms.cortex_chat_result("abc123"))
    assert running["status"] == "running" and running["elapsed_s"] == 22

    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "job_id": "abc123", "status": "done",
                   "reply": "You shipped the notes fix.", "cost_usd": 0.04}))
    done = asyncio.run(ms.cortex_chat_result("abc123"))
    assert done["status"] == "done"
    assert done["reply"] == "You shipped the notes fix."


def test_chat_result_reports_core_side_failure(gw, monkeypatch, as_writer):
    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "status": "error", "error": "budget spent"}))

    res = asyncio.run(ms.cortex_chat_result("abc123"))

    assert res["status"] == "error"
    assert "budget" in res["error"]


# ── the synchronous path + its heartbeat ─────────────────────────────

def test_fast_reply_comes_back_directly(gw, monkeypatch, as_writer):
    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "reply": "short answer"}))

    res = asyncio.run(ms.cortex_chat("ping", _Ctx()))

    assert res["reply"] == "short answer"


def test_slow_reply_emits_progress_while_waiting(gw, monkeypatch, as_writer):
    # Shorten the heartbeat so the test does not take 10 real seconds.
    monkeypatch.setattr(ms, "_CHAT_HEARTBEAT_S", 0.02)
    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "reply": "considered answer"}), delay=0.12)
    ctx = _Ctx()

    res = asyncio.run(ms.cortex_chat("think hard", ctx))

    assert res["reply"] == "considered answer"
    assert ctx.progress, "no heartbeat sent during a slow reply"
    assert "thinking" in ctx.progress[0][1]


def test_a_client_that_cannot_receive_progress_still_gets_the_reply(
        gw, monkeypatch, as_writer):
    monkeypatch.setattr(ms, "_CHAT_HEARTBEAT_S", 0.02)
    _stub_core(monkeypatch, lambda r: httpx.Response(
        200, json={"ok": True, "reply": "answer anyway"}), delay=0.08)

    res = asyncio.run(ms.cortex_chat("think", _DeafCtx()))

    assert res["reply"] == "answer anyway"


def test_sync_timeout_points_at_the_job_pattern(gw, monkeypatch, as_writer):
    # MockTransport does not enforce httpx read timeouts, so raise the
    # exception the real client would raise rather than pretending a slow
    # stub is a timeout.
    def _boom(request):
        raise httpx.ReadTimeout("read timed out", request=request)
    monkeypatch.setattr(ms, "_CHAT_HEARTBEAT_S", 0.02)
    _stub_core(monkeypatch, _boom)

    res = asyncio.run(ms.cortex_chat("think forever", _Ctx()))

    assert res["ok"] is False
    # the failure must teach the caller the way out, not just say "timeout"
    assert "cortex_chat_start" in res["hint"]


# ── gating ───────────────────────────────────────────────────────────

def test_chat_needs_an_approved_connection(gw, monkeypatch, as_reader):
    called = []
    _stub_core(monkeypatch, lambda r: called.append(1) or httpx.Response(
        200, json={"ok": True}))

    for res in (asyncio.run(ms.cortex_chat("hi", _Ctx())),
                asyncio.run(ms.cortex_chat_start("hi")),
                asyncio.run(ms.cortex_chat_result("x"))):
        assert res["ok"] is False
        assert "approved connection" in res["error"]
    assert not called, "an ungranted caller reached the core"


# ── the tools exist and say the right thing ──────────────────────────

def test_chat_tools_are_on_the_surface(gw):
    names = {t.name for t in asyncio.run(ms.mcp.list_tools())}
    assert {"cortex_chat", "cortex_chat_start",
            "cortex_chat_result"} <= names


def test_chat_docstring_teaches_the_poll_pattern(gw):
    tools = {t.name: t for t in asyncio.run(ms.mcp.list_tools())}
    desc = tools["cortex_chat"].description
    # An AI that only reads the tool list must still learn the escape hatch.
    assert "cortex_chat_start" in desc
    assert "cortex_chat_result" in desc
