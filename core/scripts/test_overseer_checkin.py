"""The check-in fires on data, not on a timer; fossils age out;
the origin counter stops crying wolf.

Added 2026-08-09 (Tory's decision on the overseer's June ask, note
2110). What has to hold:

  check-in (loop._maybe_checkin):
  - first run anchors the high-water mark and writes NOTHING
  - no new owner data: silence, no note
  - a new note (e.g. phone sync) fires exactly one check-in through the
    core upsert contract, with the delta counts in the body
  - the check-in's own note can never trigger the next one
  - inside loop_checkin_min_hours the check-in waits WITHOUT advancing
    the mark, so the eventual note still reports everything
  - a new journal entry is also a trigger

  reminder aging (CoreMemoryRO.open_reminders):
  - max_age_days drops old reminder notes from the "open" view;
    None keeps the unbounded legacy behavior

  origin distribution (OverseerDB.gist_origin_distribution):
  - tags-table source:/project: still win
  - notes-digest gists (period_label user-notes:*) bucket as
    digest:user-notes instead of untagged
  - project_tag-only gists bucket as rollup:<tag>
  - a truly bare gist still counts untagged

Run: python scripts/test_overseer_checkin.py
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "overseer"))
sys.path.insert(0, str(ROOT / "src"))

from core_memory_ro import CoreMemoryRO      # noqa: E402
from overseer_db import OverseerDB           # noqa: E402
from plugin_api import PluginConfig          # noqa: E402
from loop import OverseerLoop                # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def _utc(offset_hours=0):
    return (datetime.now(timezone.utc)
            + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class _UpsertRec:
    """Stands in for the loop's core-upsert write path. Records the
    call AND inserts the row like the real core would, so max(id)
    moves exactly as in production."""

    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    def __call__(self, table, data):
        self.calls.append((table, dict(data)))
        if table == "notes":
            cur = self.conn.execute(
                "INSERT INTO notes (content, tags, project, note_type, "
                "source) VALUES (?, ?, ?, ?, ?)",
                (data.get("content", ""), data.get("tags", ""),
                 data.get("project", ""), data.get("note_type", "note"),
                 data.get("source", "")))
            self.conn.commit()
            return {"ok": True, "id": cur.lastrowid}
        return {"ok": True, "id": 1}


def main():
    tmp = tempfile.mkdtemp(prefix="overseer-checkin-test-")
    path = str(Path(tmp) / "test.db")
    db = OverseerDB(path)
    # Core-side tables the overseer only reads (plus summaries_gist,
    # which on a fresh file predates the OverseerDB schema string).
    db._conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, tags TEXT DEFAULT '',
            project TEXT DEFAULT '', note_type TEXT DEFAULT 'note',
            source TEXT DEFAULT 'ble', session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS summaries_gist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_label TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'med',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            project_tag TEXT NOT NULL DEFAULT '');
    """)
    db._conn.commit()
    core = CoreMemoryRO(path)
    try:
        run_checkin_checks(db, core)
        run_reminder_checks(db, core)
        run_origin_checks(db)
    finally:
        core.close() if hasattr(core, "close") else None
        db.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("all passed")


def _mk_loop(db, core, **cfg_extra):
    cfg = {"loop_checkin_enabled": True, "loop_checkin_min_hours": 4.0}
    cfg.update(cfg_extra)
    loop = OverseerLoop(db=db, llm=None, core_memory=core,
                        config=PluginConfig(cfg),
                        log=logging.getLogger("test.checkin"))
    rec = _UpsertRec(db._conn)
    loop._core_upsert = rec
    return loop, rec


def run_checkin_checks(db, core):
    loop, rec = _mk_loop(db, core)

    print("anchor rule:")
    s: dict = {"errors": []}
    loop._maybe_checkin(s)
    check("first run anchors, writes nothing",
          s.get("checkin") == "anchored" and rec.calls == [])
    check("mark stored",
          db.get_overseer_state(loop.CHECKIN_MARK_KEY) is not None)

    print("silence without data:")
    s = {"errors": []}
    loop._maybe_checkin(s)
    check("no new data means no note",
          s.get("checkin") == "no-new-data" and rec.calls == [])

    print("a phone-sync note fires it:")
    db._conn.execute("INSERT INTO notes (content, source) "
                     "VALUES ('synced from phone', 'mobile')")
    db._conn.commit()
    # Age the mark past the cooldown so the trigger is the only gate.
    mark = json.loads(db.get_overseer_state(loop.CHECKIN_MARK_KEY))
    mark["at"] = _utc(-5)
    db.set_overseer_state(loop.CHECKIN_MARK_KEY, json.dumps(mark))
    s = {"errors": []}
    loop._maybe_checkin(s)
    check("check-in written", s.get("checkin") == "written", str(s))
    check("routed through the core upsert",
          rec.calls and rec.calls[0][0] == "notes")
    body = rec.calls[0][1]
    check("note_type is checkin, not reminder",
          body.get("note_type") == "checkin")
    check("pre-tagged so the auto-tagger skips it",
          "overseer-check-in" in body.get("tags", ""))
    check("body names the trigger", "1 new note" in body.get("content", ""))

    print("its own note never re-triggers:")
    mark = json.loads(db.get_overseer_state(loop.CHECKIN_MARK_KEY))
    mark["at"] = _utc(-5)
    db.set_overseer_state(loop.CHECKIN_MARK_KEY, json.dumps(mark))
    s = {"errors": []}
    loop._maybe_checkin(s)
    check("checkin note excluded from the trigger",
          s.get("checkin") == "no-new-data" and len(rec.calls) == 1)

    print("cooldown accumulates instead of dropping:")
    db._conn.execute("INSERT INTO notes (content, source) "
                     "VALUES ('during cooldown', 'mobile')")
    db._conn.commit()
    mark = json.loads(db.get_overseer_state(loop.CHECKIN_MARK_KEY))
    mark["at"] = _utc(-1)           # 1h ago < 4h floor
    db.set_overseer_state(loop.CHECKIN_MARK_KEY, json.dumps(mark))
    s = {"errors": []}
    loop._maybe_checkin(s)
    check("inside the floor it waits", s.get("checkin") == "cooldown"
          and len(rec.calls) == 1)
    mark = json.loads(db.get_overseer_state(loop.CHECKIN_MARK_KEY))
    mark["at"] = _utc(-5)
    db.set_overseer_state(loop.CHECKIN_MARK_KEY, json.dumps(mark))
    s = {"errors": []}
    loop._maybe_checkin(s)
    check("after the floor it reports the held data",
          s.get("checkin") == "written"
          and "1 new note" in rec.calls[-1][1]["content"])

    print("journal entries also trigger:")
    db._conn.execute("INSERT INTO human_journal_entries (text) "
                     "VALUES ('evening reflection')")
    db._conn.commit()
    mark = json.loads(db.get_overseer_state(loop.CHECKIN_MARK_KEY))
    mark["at"] = _utc(-5)
    db.set_overseer_state(loop.CHECKIN_MARK_KEY, json.dumps(mark))
    s = {"errors": []}
    loop._maybe_checkin(s)
    check("journal trigger fires", s.get("checkin") == "written")
    check("body names the journal entry",
          "1 new journal entry" in rec.calls[-1][1]["content"])


def run_reminder_checks(db, core):
    print("reminder aging:")
    old = (datetime.now(timezone.utc)
           - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    db._conn.execute(
        "INSERT INTO notes (content, note_type, created_at) "
        "VALUES ('ancient pi-era check-in ask', 'reminder', ?)", (old,))
    db._conn.execute(
        "INSERT INTO notes (content, note_type) "
        "VALUES ('fresh reminder', 'reminder')")
    db._conn.commit()
    aged = core.open_reminders(limit=50, max_age_days=45)
    check("old fossil aged out",
          all("ancient" not in r["content"] for r in aged))
    check("fresh reminder stays",
          any("fresh" in r["content"] for r in aged))
    legacy = core.open_reminders(limit=50)
    check("no cutoff keeps legacy behavior",
          any("ancient" in r["content"] for r in legacy))


def run_origin_checks(db):
    print("origin distribution honesty:")
    c = db._conn
    c.execute("INSERT INTO summaries_gist (period_label, body) "
              "VALUES ('sess-1', 'an import gist')")
    tagged_id = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    c.execute("INSERT INTO tags (table_name, row_id, tag) "
              "VALUES ('summaries_gist', ?, 'source:claude-code')",
              (tagged_id,))
    c.execute("INSERT INTO summaries_gist (period_label, body) "
              "VALUES ('user-notes:2026-08-08', 'a day digest')")
    c.execute("INSERT INTO summaries_gist (period_label, body, project_tag) "
              "VALUES ('rollup', 'a rollup', 'mobile-voice')")
    c.execute("INSERT INTO summaries_gist (period_label, body) "
              "VALUES ('bare', 'no signals at all')")
    c.commit()
    dist = db.recent_gist_source_distribution(recent_n=10)
    by = dist["by_origin"]
    check("tags-table source still wins",
          by.get("source:claude-code") == 1, str(dist))
    check("notes digests bucket by period_label",
          by.get("digest:user-notes") == 1, str(dist))
    check("project_tag gists bucket as rollups",
          by.get("rollup:mobile-voice") == 1, str(dist))
    check("only the truly bare gist is untagged",
          dist["untagged"] == 1, str(dist))


if __name__ == "__main__":
    main()
