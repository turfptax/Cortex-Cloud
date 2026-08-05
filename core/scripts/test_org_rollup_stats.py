"""The org rollup has to STAMP stats_updated_at, not just recompute stats.

Added 2026-08-05. org_summaries.stats_updated_at is declared
`DEFAULT (datetime('now'))`, and upsert_row does a partial UPDATE of
exactly the columns it is handed. The rollup handed it member counts and
hours but never the timestamp, so every org row kept the timestamp from
the day it was first inserted while its numbers were rewritten on every
tick. The column that exists to answer "are these stats fresh?" answered
with the backfill date instead, for a week, while the tick counter
truthfully reported every row written.

That combination is the dangerous one: a counter that says the work
happened and a column that says it did not. Two separate readers of this
data reached the wrong conclusion before the cause was found.

What has to hold:
  - a first rollup stamps stats_updated_at
  - a SECOND rollup over an existing row MOVES it (the partial-UPDATE
    path, which is the one that was broken; the INSERT path always
    worked because of the column default)
  - the stamp is UTC in SQLite's own datetime('now') shape, so it sorts
    and compares against rows written by SQL defaults
  - real stats still land, so the fix did not displace the payload

Run: python scripts/test_org_rollup_stats.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))
sys.path.insert(0, str(_HERE.parent / "plugins" / "overseer"))

from cortex_db import CortexDB  # noqa: E402
import org_rollup  # noqa: E402

FAILURES = []

STALE = "2020-01-01 00:00:00"


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


class CoreShim:
    """CoreMemoryRO stand-in reading the same file the writer writes."""

    def __init__(self, db):
        self._db = db

    def query(self, sql, params=()):
        cur = self._db._conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


class DbShim:
    """OverseerDB stand-in: the rollup only reads project summaries."""

    def get_project_summary(self, tag):
        return None


class Budget:
    def exhausted(self):
        return False

    def charge(self, *a, **kw):
        pass


class Llm:
    def complete(self, *a, **kw):
        raise AssertionError("stats pass must not call the model")


def main():
    tmp = Path(tempfile.mkdtemp())
    db = CortexDB(str(tmp / "cortex.db"))

    db._conn.execute(
        "INSERT INTO organizations (tag, name, org_type, is_active,"
        " is_default) VALUES ('acme', 'Acme', 'company', 1, 0)")
    db._conn.execute(
        "INSERT INTO projects (tag, name, org_tag, status, total_hours,"
        " last_touched) VALUES ('widget', 'Widget', 'acme', 'active',"
        " 4.5, '2026-08-01 10:00:00')")
    db._conn.commit()

    def upsert(table, data):
        db.upsert_row(table, data)
        return {"ok": True}

    def rollup():
        summary = {"errors": []}
        org_rollup.run_org_rollup(
            core=CoreShim(db), db=DbShim(), llm=Llm(), cfg={},
            budget=Budget(), summary=summary, upsert=upsert,
            max_narratives=0)
        return summary

    def row():
        return CoreShim(db).query(
            "SELECT * FROM org_summaries WHERE org_tag = 'acme'")[0]

    print("\nscenario: the first rollup stamps the row")
    s1 = rollup()
    check("no errors", s1["errors"] == [], str(s1["errors"]))
    # The schema seeds a default 'unsorted' bucket, so the live count is
    # every active org, not just the one this test inserted.
    n_orgs = CoreShim(db).query(
        "SELECT COUNT(*) AS n FROM organizations WHERE is_active = 1")[0]["n"]
    check("every active org counted",
          s1.get("org_rollup_stats_written") == n_orgs,
          "{} orgs, counter says {}".format(n_orgs,
                                            s1.get("org_rollup_stats_written")))
    r1 = row()
    check("stats landed", r1["member_count"] == 1
          and abs(float(r1["total_hours"]) - 4.5) < 0.001, str(r1))
    check("stats_updated_at is set", bool(r1["stats_updated_at"]), str(r1))

    print("\nscenario: a SECOND rollup moves a stale timestamp")
    # Backdate exactly the way the live row was stuck, so this reproduces
    # the reported symptom rather than a timing artifact.
    db._conn.execute(
        "UPDATE org_summaries SET stats_updated_at = ? WHERE org_tag = 'acme'",
        (STALE,))
    db._conn.commit()
    check("backdated", row()["stats_updated_at"] == STALE)

    rollup()
    r2 = row()
    check("the stale timestamp was refreshed",
          r2["stats_updated_at"] != STALE,
          "still {!r}".format(r2["stats_updated_at"]))

    print("\nscenario: the stamp is UTC in SQLite's own shape")
    got = r2["stats_updated_at"]
    parsed = None
    try:
        parsed = datetime.strptime(got, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    check("parses as 'YYYY-MM-DD HH:MM:SS'", parsed is not None, repr(got))
    if parsed is not None:
        drift = abs((datetime.now(timezone.utc).replace(tzinfo=None)
                     - parsed).total_seconds())
        check("within a minute of now in UTC", drift < 60,
              "{}s of drift".format(int(drift)))

    print("\nscenario: the payload survived the fix")
    check("member count still right", r2["member_count"] == 1, str(r2))
    check("hours still right", abs(float(r2["total_hours"]) - 4.5) < 0.001,
          str(r2))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all org-rollup stamp checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
