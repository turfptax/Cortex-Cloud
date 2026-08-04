"""Async chat jobs survive the caller hanging up.

Added 2026-08-04. A full overseer reply takes 45-70s while MCP clients give
up around 60s, so /chat/start hands the work to a daemon thread and returns
a job id. What has to hold:

  - start returns immediately, long before the reply exists
  - the reply lands even though nobody is waiting on the HTTP call
  - a crash in the worker becomes status=error, not a job stuck on running
    forever
  - the busy guard refuses a third concurrent job
  - the OWNER's active chat thread pointer is never moved. External AI turns
    must not redirect the next message the owner types into the Hub.

Run: python scripts/test_chat_jobs.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "overseer"))
sys.path.insert(0, str(ROOT / "src"))

from overseer_db import OverseerDB  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


class FakePlugin:
    """The parts of the overseer plugin the job routes actually touch."""

    def __init__(self, db, responder):
        self.overseer_db = db
        self.llm = object()
        self.core_memory = object()
        self._responder = responder

        class _Api:
            class log:
                @staticmethod
                def exception(*a, **k):
                    pass

                @staticmethod
                def warning(*a, **k):
                    pass
        self.api = _Api()

    def _sibling_daily_cap(self):
        return 5


def _bind_routes(plugin_cls):
    """Borrow the real route implementations onto the fake plugin."""
    import __init__ as overseer_mod  # the plugin module
    for name in ("_http_chat_start", "_http_chat_job", "_chat_job_get",
                 "_chat_job_put", "_chat_jobs_running"):
        setattr(plugin_cls, name, getattr(overseer_mod.OverseerPlugin, name))
    for name in ("_CHAT_JOB_PREFIX", "_CHAT_JOB_MAX_RUNNING",
                 "_CHAT_JOB_TTL_DAYS", "_MCP_THREAD_TITLE"):
        setattr(plugin_cls, name, getattr(overseer_mod.OverseerPlugin, name))
    return overseer_mod


def main():
    tmp = Path(tempfile.mkdtemp())
    db = OverseerDB(str(tmp / "overseer.db"))

    # The worker calls module-level respond_to_message; swap it per scenario.
    overseer_mod = _bind_routes(FakePlugin)

    print("\nscenario: a slow reply lands after start returns")
    gate = threading.Event()

    def slow_responder(**kw):
        gate.wait(timeout=5)
        return {"ok": True, "user_message_id": 1, "assistant_message_id": 2,
                "cost_usd": 0.04, "model": "opus"}

    overseer_mod.respond_to_message = slow_responder
    p = FakePlugin(db, slow_responder)

    owner_thread = db.create_chat_thread("Owner's Hub chat")
    active_before = db.get_overseer_state("chat_active_thread_id")

    t0 = time.time()
    started = p._http_chat_start({"message": "think about this"})
    elapsed = time.time() - t0
    check("start returns immediately", elapsed < 1.0, f"took {elapsed:.2f}s")
    check("start reports running", started.get("status") == "running")
    job_id = started.get("job_id")
    check("start returns a job id", bool(job_id))

    mid = p._http_chat_job({"id": job_id})
    check("job is running before the worker finishes",
          mid.get("status") == "running", str(mid))
    check("running reply carries elapsed_s", "elapsed_s" in mid)

    # Seed the assistant row the worker will point at.
    db._conn.execute(
        "INSERT INTO chat_messages (id, thread_id, role, content, model,"
        " cost_usd) VALUES (2, ?, 'assistant', 'the considered answer',"
        " 'opus', 0.04)", (started.get("thread_id"),))
    db._safe_commit()

    gate.set()
    for _ in range(50):
        done = p._http_chat_job({"id": job_id})
        if done.get("status") != "running":
            break
        time.sleep(0.1)
    check("job completes", done.get("status") == "done", str(done))
    check("reply is returned from the chat thread",
          done.get("reply") == "the considered answer", str(done.get("reply")))

    active_after = db.get_overseer_state("chat_active_thread_id")
    check("the owner's active thread pointer never moved",
          str(active_before) == str(active_after),
          f"{active_before!r} -> {active_after!r}")
    check("the MCP thread is a separate thread",
          str(started.get("thread_id")) != str(owner_thread))

    print("\nscenario: a worker crash becomes status=error")

    def boom(**kw):
        raise RuntimeError("model refused")
    overseer_mod.respond_to_message = boom
    p2 = FakePlugin(db, boom)
    s2 = p2._http_chat_start({"message": "explode"})
    for _ in range(50):
        r2 = p2._http_chat_job({"id": s2["job_id"]})
        if r2.get("status") != "running":
            break
        time.sleep(0.1)
    check("crash surfaces as error, not eternal running",
          r2.get("status") == "error", str(r2))
    check("the error text reaches the caller",
          "model refused" in (r2.get("error") or ""), str(r2.get("error")))

    print("\nscenario: the busy guard")
    stall = threading.Event()

    def staller(**kw):
        stall.wait(timeout=5)
        return {"ok": True, "assistant_message_id": 2}
    overseer_mod.respond_to_message = staller
    p3 = FakePlugin(db, staller)
    a = p3._http_chat_start({"message": "one"})
    b = p3._http_chat_start({"message": "two"})
    c = p3._http_chat_start({"message": "three"})
    check("first two jobs accepted", a.get("ok") and b.get("ok"))
    check("third job refused while two are running", c.get("ok") is False,
          str(c))
    check("refusal tells the caller when to retry", "retry_after_s" in c)
    stall.set()

    print("\nscenario: unknown job id")
    check("unknown job is reported, not crashed",
          p._http_chat_job({"id": "nope"}).get("error") == "unknown job")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all chat-job checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
