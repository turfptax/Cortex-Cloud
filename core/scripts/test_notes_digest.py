"""The notes digest gleans every capture route, not just the phone.

Added 2026-08-04. The step used to filter `source = 'mobile'`, which is only
what the phone sync writes. The wearable and desktop write 'ble', the voice
journal 'voice', the web Hub 'hub', gateway ingest 'cortex' or
'ai-generated'. All of those were silently dropped, so the one step that
gleans the owner's own words was digesting almost nothing while reporting
success.

What has to hold:
  - every capture source is digested, not just 'mobile'
  - bulk archives (twitter and friends) are excluded, because they are
    imports rather than captures and would cost hundreds of LLM calls
  - only COMPLETE local days are digested; today is left alone
  - the first run seeds its high-water mark instead of eating years of
    history in one tick
  - an empty model reply still advances the mark, so one bad day cannot
    wedge the queue forever
  - a database WITHOUT local_created_at still works (that column is added at
    runtime by timestamp_localizer, not by the DDL, so fixtures and fresh
    deploys lack it)

Run: python scripts/test_notes_digest.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "overseer"))
sys.path.insert(0, str(ROOT / "src"))

import mobile_digest  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


class FakeCore:
    """CoreMemoryRO stand-in over a temp cortex.db."""

    def __init__(self, path, *, with_local_col=True):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        cols = ("id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT,"
                " note_type TEXT, project TEXT, source TEXT,"
                " session_id TEXT, created_at TEXT")
        if with_local_col:
            cols += ", local_created_at TEXT"
        self._conn.execute(f"CREATE TABLE notes ({cols})")
        self._with_local = with_local_col

    def add(self, content, *, source, day, session_id=None, hhmm="09:30"):
        ts = f"{day} {hhmm}:00"
        if self._with_local:
            self._conn.execute(
                "INSERT INTO notes (content, note_type, project, source,"
                " session_id, created_at, local_created_at)"
                " VALUES (?,'note','',?,?,?,?)",
                (content, source, session_id, ts, ts))
        else:
            self._conn.execute(
                "INSERT INTO notes (content, note_type, project, source,"
                " session_id, created_at) VALUES (?,'note','',?,?,?)",
                (content, source, session_id, ts))
        self._conn.commit()

    def query(self, sql, params=()):
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]


class FakeDB:
    def __init__(self):
        self.state = {}
        self.gists = []

    def get_overseer_state(self, k, default=None):
        return self.state.get(k, default)

    def set_overseer_state(self, k, v):
        self.state[k] = v

    def add_gist(self, text, **kw):
        self.gists.append({"text": text, **kw})
        return len(self.gists)


class FakeLLM:
    def __init__(self, reply="digest line"):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return {"ok": True, "text": self.reply}


def _days_ago(n):
    return (datetime.now().astimezone()
            - timedelta(days=n)).strftime("%Y-%m-%d")


def main():
    tmp = Path(tempfile.mkdtemp())

    print("\nscenario: every capture source is gleaned, archives are not")
    core = FakeCore(tmp / "a.db")
    d1, d2 = _days_ago(2), _days_ago(1)
    core.add("wearable thought", source="ble", day=d1)
    core.add("phone capture", source="mobile", day=d1)
    core.add("voice journal", source="voice", day=d1)
    core.add("web hub row", source="hub", day=d2)
    core.add("agent observation", source="ai-generated", day=d2)
    core.add("owner token write", source="cortex", day=d2)
    core.add("an old tweet", source="twitter", day=d1)
    core.add("takeout row", source="google-takeout", day=d2)
    # session-attached notes belong to the session gist path, not here
    core.add("in a session", source="ble", day=d1, session_id="sess-1")

    db, llm = FakeDB(), FakeLLM()
    db.set_overseer_state(mobile_digest.STATE_KEY, _days_ago(5))
    out = mobile_digest.run_notes_digest(core=core, db=db, llm=llm, cfg={})

    check("both complete days digested", out["days_digested"] == 2, str(out))
    bodies = " ".join(g["text"] for g in db.gists)
    prompts_seen = llm.last_prompt
    check("gists are labelled user-notes",
          all(g["period_label"].startswith("user-notes:") for g in db.gists),
          str([g["period_label"] for g in db.gists]))

    # Inspect what actually reached the model on the second day.
    check("web hub capture reached the model", "web hub row" in prompts_seen,
          prompts_seen[:200])
    check("AI-written observation reached the model",
          "agent observation" in prompts_seen)
    check("owner token write reached the model",
          "owner token write" in prompts_seen)
    check("archive rows excluded", "takeout row" not in prompts_seen)
    check("source is shown to the model so it can weigh provenance",
          "ai-generated" in prompts_seen)

    print("\nscenario: today is never digested half-finished")
    core2 = FakeCore(tmp / "b.db")
    core2.add("yesterday", source="ble", day=_days_ago(1))
    core2.add("today, still happening", source="ble", day=_days_ago(0))
    db2, llm2 = FakeDB(), FakeLLM()
    db2.set_overseer_state(mobile_digest.STATE_KEY, _days_ago(5))
    out2 = mobile_digest.run_notes_digest(core=core2, db=db2, llm=llm2, cfg={})
    check("only the complete day is digested", out2["days_digested"] == 1,
          str(out2))
    check("today untouched", "still happening" not in llm2.last_prompt)

    print("\nscenario: the first run seeds instead of eating all history")
    core3 = FakeCore(tmp / "c.db")
    core3.add("ancient", source="ble", day="2023-04-01")
    core3.add("also ancient", source="ble", day="2024-06-15")
    core3.add("recent", source="ble", day=_days_ago(1))
    db3, llm3 = FakeDB(), FakeLLM()          # no high-water mark at all
    out3 = mobile_digest.run_notes_digest(core=core3, db=db3, llm=llm3,
                                          cfg={"loop_notes_digest_seed_days": 7})
    check("history beyond the seed window is left for the backfill",
          out3["days_digested"] == 1, str(out3))
    check("the recent day still got digested",
          db3.gists and db3.gists[0]["period_label"].endswith(_days_ago(1)),
          str([g["period_label"] for g in db3.gists]))

    print("\nscenario: an empty reply cannot wedge the queue")
    core4 = FakeCore(tmp / "d.db")
    core4.add("one", source="ble", day=_days_ago(2))
    core4.add("two", source="ble", day=_days_ago(1))
    db4 = FakeDB()
    db4.set_overseer_state(mobile_digest.STATE_KEY, _days_ago(5))
    mobile_digest.run_notes_digest(core=core4, db=db4, llm=FakeLLM(reply="  "),
                                   cfg={})
    check("the mark advanced past the empty day",
          db4.get_overseer_state(mobile_digest.STATE_KEY) == _days_ago(1),
          str(db4.state))

    print("\nscenario: a database without local_created_at (old schema)")
    core5 = FakeCore(tmp / "e.db", with_local_col=False)
    core5.add("no local column here", source="ble", day=_days_ago(1))
    db5, llm5 = FakeDB(), FakeLLM()
    db5.set_overseer_state(mobile_digest.STATE_KEY, _days_ago(5))
    try:
        out5 = mobile_digest.run_notes_digest(core=core5, db=db5, llm=llm5,
                                              cfg={})
        check("old schema falls back to created_at",
              out5["days_digested"] == 1, str(out5))
    except sqlite3.OperationalError as e:
        check("old schema falls back to created_at", False, str(e))

    print("\nscenario: the legacy phone-only path still works")
    core6 = FakeCore(tmp / "f.db")
    core6.add("phone only", source="mobile", day=_days_ago(1))
    core6.add("wearable", source="ble", day=_days_ago(1))
    db6, llm6 = FakeDB(), FakeLLM()
    db6.set_overseer_state(mobile_digest.LEGACY_STATE_KEY, _days_ago(5))
    mobile_digest.run_mobile_digest(core=core6, db=db6, llm=llm6)
    check("legacy path digests only mobile", "wearable" not in llm6.last_prompt)
    check("legacy path uses its own state key",
          db6.get_overseer_state(mobile_digest.LEGACY_STATE_KEY) == _days_ago(1)
          and mobile_digest.STATE_KEY not in db6.state, str(db6.state))
    check("legacy path keeps its own label",
          db6.gists[0]["period_label"].startswith("mobile-notes:"),
          str(db6.gists[0]["period_label"]))

    backfill_checks()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all notes-digest checks passed")
    return 0




def backfill_checks():
    """The backfill digests old days without disturbing the forward step."""
    import tempfile as _tf
    tmp = Path(_tf.mkdtemp())
    print("\nscenario: backfill targets one old day and leaves the mark alone")
    core = FakeCore(tmp / "bf.db")
    core.add("february thought", source="ble", day="2026-02-25")
    core.add("march thought", source="ble", day="2026-03-10")
    core.add("recent", source="ble", day=_days_ago(1))
    db, llm = FakeDB(), FakeLLM()
    db.set_overseer_state(mobile_digest.STATE_KEY, _days_ago(3))
    before = db.get_overseer_state(mobile_digest.STATE_KEY)

    out = mobile_digest.run_notes_digest(
        core=core, db=db, llm=llm, cfg={}, _only_day="2026-02-25")

    check("backfill digested exactly the requested day",
          out["days_digested"] == 1, str(out))
    check("it used the old day's content",
          "february thought" in llm.last_prompt)
    check("the forward high-water mark did NOT move",
          db.get_overseer_state(mobile_digest.STATE_KEY) == before,
          "{!r} -> {!r}".format(before,
                                db.get_overseer_state(mobile_digest.STATE_KEY)))
    check("gist is labelled for the historical day",
          db.gists[0]["period_label"] == "user-notes:2026-02-25",
          str(db.gists[0]["period_label"]))

    print("\nscenario: routing can be skipped for old days")
    core2 = FakeCore(tmp / "bf2.db")
    core2.add("old", source="ble", day="2026-02-26")
    db2, llm2 = FakeDB(), FakeLLM()
    out2 = mobile_digest.run_notes_digest(
        core=core2, db=db2, llm=llm2, cfg={}, _only_day="2026-02-26",
        _route_questions=False)
    check("gist still written with routing off",
          out2["days_digested"] == 1 and len(db2.gists) == 1, str(out2))

if __name__ == "__main__":
    raise SystemExit(main())
