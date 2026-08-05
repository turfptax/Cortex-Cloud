"""One-time move of the overseer's tables into the corpus database.

Delete this script once the merge is verified. It is not boot code and
nothing imports it.

WHY THE SHAPE IS COPY, VERIFY, THEN DROP
----------------------------------------
The obvious design is one transaction per table that copies and drops
together, so a crash can never leave a table in one file and not the
other. SQLite will happily accept that transaction, and it is NOT safe
here: both databases run in WAL mode, and SQLite does not guarantee
atomic commit across attached databases when any of them is in WAL. The
COMMIT can succeed against one file and not the other.

So the guarantee is built differently:

  Phase A  copy each table into the destination, one transaction each,
           touching the DESTINATION ONLY. Re-runnable: a table that is
           already there from a partial run is rebuilt from scratch,
           because the source is still authoritative until Phase C.
  Phase B  verify every table by row count AND a content checksum.
           Any mismatch aborts the run before a single row is dropped.
  Phase C  drop the source tables in ONE transaction that touches the
           SOURCE ONLY, which is a plain single-database transaction and
           therefore genuinely atomic.

The worst outcome is "both copies exist", which is safe, obvious, and
re-runnable. The outcome that can never happen is "neither copy exists".

Phase C runs in the SAME invocation as Phase A. OPT-10 deferred its
drops to "one release later", the drops never happened, and the leftover
shells shadowed the live tables for weeks because SQLite resolves `main`
before attached schemas. Deferring is the bug.

ORDER WITHIN A TABLE
--------------------
CREATE TABLE, INSERT, CREATE INDEX, CREATE TRIGGER. The triggers come
last on purpose. Nearly all of them are timestamp localizers that fire
AFTER INSERT and rewrite local_* on the row they just saw. Arming them
before the data lands would make every copied row recompute its local
timestamp against the CONTAINER's timezone instead of preserving what is
stored. Rows with a NULL local_* stay NULL, which is honest; the app's
own localizer fills them later on its own terms.

PRECONDITIONS: THE CODE CHANGE COMES FIRST
------------------------------------------
Moving the data before changing the code produces a silent, total
failure, so this script refuses to do it.

Two paths conspire. The plugin loader builds each plugin's database path
from the plugin NAME (`plugins_runtime.py`, `f"{manifest.name}.db"`) and
opens it in _build_api, which runs BEFORE the plugin's on_load ever reads
its config. So the source file is recreated on every boot whether or not
it was deleted and whether or not its env var is set. Then OverseerDB's
constructor runs its whole schema, 80-odd CREATE TABLE IF NOT EXISTS,
against that fresh file.

The result is a full set of EMPTY tables in `main`, and SQLite resolves
`main` before attached schemas. Every unqualified read in the plugin,
the loop and chat would find the empty copy and never see the migrated
rows. No exception, no log line, no failing request: the corpus simply
reads as wiped, which is the exact failure this script's own three-phase
design exists to prevent.

Before running with --apply:
  1. OverseerDB must take the corpus connection instead of opening its
     own file.
  2. The plugin loader must not manufacture a database for this plugin.
  3. The sync plugin's overseer-tagged routes must point at the corpus
     (four PUSH kinds plus the bell, vector and voice-import reads),
     or the phone's writes land in a dead file and still report success.
  4. The gateway's read-only ATTACH and its _ATTACH_SCHEMAS tuple must be
     changed together; changing one without the other breaks table
     lookup rather than failing loudly.
  5. The scheduled tick job is a SEPARATE Azure resource. Scaling the
     app down does not stop it, and it writes.

Usage:
  python merge_overseer_into_cortex.py --source o.db --dest c.db
  python merge_overseer_into_cortex.py --source o.db --dest c.db --apply
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

# Litestream's own per-database bookkeeping. Each database needs its own.
KEEP = ("_litestream_lock", "_litestream_seq")

# Superseded, empty, and actively in the way. `people` has no CREATE
# TABLE left anywhere in the codebase and no SQL consumer; the People
# pillar lives in the destination as overseer_people. It also carries
# trigger names that already exist on the destination (attached to that
# database's own _migrated_people residue), and trigger names are global
# per database, so copying it would fail outright.
DROP_EXTRA = ("people",)

# sqlite-vec virtual table plus its shadow tables. The vec0 table is
# copied by its OWN path, not the generic one: the shadow tables are
# managed by the extension and must never be touched directly, so only
# the virtual table is named and its shadows follow automatically.
#
# Leaving the vectors behind was considered and rejected. It would mean
# overseer.db has to survive, which is the entire thing this merge
# exists to stop. Re-embedding was the fallback; it is not needed. A
# direct copy of 3,943 vectors took 0.1s in rehearsal and a k-nearest
# probe against the copy returned the same neighbours at the same
# distances as the source.
VEC_PREFIX = "vec_"
VEC_TABLE = "vec_gists"


def connect(path, *, readonly=False, vectors=False):
    uri = "file:{}?mode=ro".format(path) if readonly else path
    conn = sqlite3.connect(uri, uri=readonly, timeout=30)
    conn.row_factory = sqlite3.Row
    if vectors:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    return conn


def copy_vectors(conn, src_objs, log):
    """Move the vec0 table. Its shadow tables come along by themselves.

    A virtual table cannot be rebuilt by copying its shadows, so this
    replays the CREATE VIRTUAL TABLE and inserts through the extension,
    which is what keeps the index consistent. Requires sqlite-vec loaded
    on BOTH connections, which is why the caller opens them that way.
    """
    ddl = next((r["sql"] for r in src_objs
                if r["type"] == "table" and r["name"] == VEC_TABLE), None)
    if not ddl:
        log("  no {} on the source, nothing to move".format(VEC_TABLE))
        return 0, 0
    conn.execute("BEGIN")
    try:
        conn.execute('DROP TABLE IF EXISTS main."{}"'.format(VEC_TABLE))
        conn.execute(ddl)
        conn.execute(
            "INSERT INTO main.{0} (gist_id, embedding)"
            " SELECT gist_id, embedding FROM src.{0}".format(VEC_TABLE))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    sn = conn.execute("SELECT COUNT(*) FROM src.{}".format(VEC_TABLE)).fetchone()[0]
    dn = conn.execute("SELECT COUNT(*) FROM main.{}".format(VEC_TABLE)).fetchone()[0]
    log("  copied {:<38} {:>8} rows".format(VEC_TABLE, dn))
    return sn, dn


def verify_vectors(conn, log):
    """Row count is not enough for an index: a vector table can hold
    every row and still return different neighbours. Probe it."""
    probe = conn.execute(
        "SELECT embedding FROM src.{} LIMIT 1".format(VEC_TABLE)).fetchone()
    if probe is None:
        return True
    q = ("SELECT gist_id FROM {}.{} WHERE embedding MATCH ? AND k = 5"
         " ORDER BY distance")
    a = [r[0] for r in conn.execute(q.format("src", VEC_TABLE), (probe[0],))]
    b = [r[0] for r in conn.execute(q.format("main", VEC_TABLE), (probe[0],))]
    ok = a == b
    log("  nearest-neighbour probe {}".format(
        "matches the source" if ok else "DIFFERS: {} vs {}".format(a, b)))
    return ok


def master(conn, schema="main"):
    return conn.execute(
        "SELECT type, name, tbl_name, sql FROM {}.sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%'".format(schema)).fetchall()


def classify(src_objs):
    """Split the source's tables into move, drop and skip."""
    tables = {r["name"]: r for r in src_objs if r["type"] == "table"}
    virtuals = {n for n, r in tables.items()
                if "CREATE VIRTUAL TABLE" in (r["sql"] or "").upper()}
    move, drop, skip = [], [], []
    for name in sorted(tables):
        if name in KEEP:
            skip.append((name, "litestream bookkeeping"))
        elif name in virtuals or name.startswith(VEC_PREFIX):
            skip.append((name, "vector table, separate decision"))
        elif name.startswith("_migrated_"):
            drop.append((name, "OPT-10 residue, superseded by its live twin"))
        elif name in DROP_EXTRA:
            drop.append((name, "superseded and blocking"))
        else:
            move.append(name)
    return move, drop, skip


def checksum(conn, table, schema="main"):
    """Order-independent content hash, so it survives a differing rowid
    order between the two files. Each row is serialized canonically and
    the per-row digests are summed, which makes the result independent
    of the order rows come back in but still sensitive to any changed,
    missing or duplicated row."""
    cur = conn.execute('SELECT * FROM "{}"."{}"'.format(schema, table))
    cols = [c[0] for c in cur.description]
    total = 0
    n = 0
    for row in cur:
        parts = []
        for c in cols:
            v = row[c]
            if v is None:
                parts.append("\x00N")
            elif isinstance(v, bytes):
                parts.append("\x00B" + v.hex())
            elif isinstance(v, float):
                parts.append("\x00F" + repr(v))
            else:
                parts.append("\x00S" + str(v))
        digest = hashlib.md5("".join(parts).encode("utf-8")).digest()
        total = (total + int.from_bytes(digest, "big")) % (1 << 128)
        n += 1
    return n, "{:032x}".format(total)


def copy_table(conn, name, src_objs, log):
    """Rebuild one table on the destination inside its own transaction.

    Touches the destination only. `conn` has the destination as main and
    the source attached as `src`.
    """
    table_sql = next(r["sql"] for r in src_objs
                     if r["type"] == "table" and r["name"] == name)
    indexes = [r["sql"] for r in src_objs if r["type"] == "index"
               and r["tbl_name"] == name and r["sql"]]
    triggers = [r["sql"] for r in src_objs if r["type"] == "trigger"
                and r["tbl_name"] == name and r["sql"]]

    conn.execute("BEGIN")
    try:
        # A leftover from an aborted run is not data, it is a partial
        # copy. The source is still authoritative until Phase C, so
        # rebuilding is always correct and always safe.
        conn.execute('DROP TABLE IF EXISTS main."{}"'.format(name))
        conn.execute(table_sql)
        conn.execute('INSERT INTO main."{0}" SELECT * FROM src."{0}"'
                     .format(name))
        for sql in indexes:
            conn.execute(sql)
        for sql in triggers:          # last: see the module docstring
            conn.execute(sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    n = conn.execute('SELECT COUNT(*) FROM main."{}"'.format(name)).fetchone()[0]
    log("  copied {:<38} {:>8} rows  ({} idx, {} trg)".format(
        name, n, len(indexes), len(triggers)))
    return n


def code_still_owns_source(log):
    """Look for the two code paths that would undo this migration.

    Neither is subtle once you know to look, and both are silent when
    they fire, so the check is cheap insurance rather than cleverness.
    Returns a list of human-readable reasons; empty means clear.

    This reads the tree the script is sitting in. If the running image
    differs from this checkout the check is meaningless, which is why
    --code-is-migrated exists as an override rather than the check being
    the only gate.
    """
    reasons = []
    here = Path(__file__).resolve().parent.parent      # core/
    runtime = here / "src" / "plugins_runtime.py"
    ov = here / "plugins" / "overseer" / "overseer_db.py"
    try:
        if runtime.is_file():
            txt = runtime.read_text(encoding="utf-8", errors="replace")
            if 'f"{manifest.name}.db"' in txt and "CortexDB(" in txt:
                reasons.append(
                    "plugins_runtime.py still builds a per-plugin database "
                    "path from the plugin NAME and opens it, which recreates "
                    "the source file on every boot no matter what the config "
                    "says.")
        if ov.is_file():
            txt = ov.read_text(encoding="utf-8", errors="replace")
            if "OVERSEER_SCHEMA_SQL" in txt and "executescript" in txt:
                reasons.append(
                    "OverseerDB still runs its full CREATE TABLE IF NOT "
                    "EXISTS schema against whatever file it opens. On a "
                    "recreated source that means empty tables in `main`, and "
                    "SQLite resolves `main` before attached schemas, so every "
                    "unqualified read would find the empty copy instead of "
                    "the migrated data.")
    except OSError:
        pass
    return reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="overseer database")
    ap.add_argument("--dest", required=True, help="corpus database")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without this it is a dry run")
    ap.add_argument("--code-is-migrated", action="store_true",
                    help="acknowledge that the app no longer opens the "
                         "source as its own database (see PRECONDITIONS)")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    src_ro = connect(args.source, readonly=True)
    src_objs = master(src_ro)
    move, drop, skip = classify(src_objs)
    views = [r for r in src_objs if r["type"] == "view"]

    dest_ro = connect(args.dest, readonly=True)
    dest_tables = {r["name"] for r in master(dest_ro) if r["type"] == "table"}

    log("PLAN")
    log("  move   {} tables".format(len(move)))
    log("  drop   {} tables".format(len(drop)))
    log("  skip   {} tables".format(len(skip)))
    log("  views  {} to recreate".format(len(views)))
    for name, why in skip:
        log("    skip {:<34} {}".format(name, why))
    for name, why in drop:
        log("    drop {:<34} {}".format(name, why))

    collisions = [n for n in move if n in dest_tables]
    if collisions:
        log("")
        log("ABORT: these already exist on the destination, so the copy "
            "would silently replace live data:")
        for n in collisions:
            log("    {}".format(n))
        return 2

    if not args.apply:
        log("")
        log("dry run. nothing written. re-run with --apply to execute.")
        return 0

    stale = code_still_owns_source(log)
    if stale and not args.code_is_migrated:
        log("")
        log("ABORT: the code in this tree still opens the source as its own")
        log("database. Moving the data now would look like it worked and")
        log("would then be undone by the next boot. See PRECONDITIONS at the")
        log("top of this file.")
        for line in stale:
            log("    " + line)
        log("")
        log("If you have genuinely done the code change and this check is")
        log("wrong, re-run with --code-is-migrated.")
        return 5
    if stale:
        log("")
        log("WARNING: the precondition check still sees the old code, and")
        log("--code-is-migrated was passed. Proceeding on your word.")

    # A writer still holding the source would make the copy a snapshot of
    # a moving target. BEGIN IMMEDIATE proves exclusivity instead of
    # assuming it.
    # vectors=True even though this connection only ever drops: DROP
    # TABLE on a vec0 table has to go through the extension so it can
    # clean up its own shadow tables, and without it the drop fails with
    # "no such module: vec0".
    src_rw = connect(args.source, vectors=True)
    try:
        src_rw.execute("BEGIN IMMEDIATE")
        src_rw.execute("ROLLBACK")
    except sqlite3.OperationalError as e:
        log("")
        log("ABORT: the source database is busy ({}). Stop the writers "
            "first.".format(e))
        return 3

    conn = connect(args.dest, vectors=True)
    conn.execute("ATTACH DATABASE ? AS src", (args.source,))

    log("")
    log("PHASE A: copy into the destination")
    t0 = time.time()
    copied = {}
    for name in move:
        copied[name] = copy_table(conn, name, src_objs, log)
    vec_src, vec_dst = copy_vectors(conn, src_objs, log)
    for v in views:
        conn.execute('DROP VIEW IF EXISTS main."{}"'.format(v["name"]))
        conn.execute(v["sql"])
        log("  view   {}".format(v["name"]))
    conn.commit()
    log("  {:.1f}s".format(time.time() - t0))

    log("")
    log("PHASE B: verify row counts and content checksums")
    bad = []
    for name in move:
        sn, sh = checksum(conn, name, "src")
        dn, dh = checksum(conn, name, "main")
        if sn != dn or sh != dh:
            bad.append((name, sn, dn, sh, dh))
            log("  MISMATCH {:<34} src={} dst={}".format(name, sn, dn))
    if vec_src != vec_dst:
        bad.append((VEC_TABLE, vec_src, vec_dst, "", ""))
        log("  MISMATCH {:<34} src={} dst={}".format(
            VEC_TABLE, vec_src, vec_dst))
    elif vec_dst and not verify_vectors(conn, log):
        bad.append((VEC_TABLE, vec_src, vec_dst, "knn", "differs"))
    if bad:
        log("")
        log("ABORT: {} table(s) did not verify. NOTHING was dropped; the "
            "source is untouched and still authoritative.".format(len(bad)))
        return 4
    log("  all {} tables match on count and content".format(len(move)))

    log("")
    log("PHASE C: drop from the source, one atomic single-database "
        "transaction")
    # Single-database transaction on purpose. This is the only way to get
    # a real all-or-nothing drop when both files are in WAL mode.
    src_rw.execute("BEGIN IMMEDIATE")
    try:
        for name, _why in drop:
            src_rw.execute('DROP TABLE IF EXISTS "{}"'.format(name))
        for name in move:
            src_rw.execute('DROP TABLE IF EXISTS "{}"'.format(name))
        for v in views:
            src_rw.execute('DROP VIEW IF EXISTS "{}"'.format(v["name"]))
        if vec_dst:
            # Only the virtual table is named. Dropping it makes the
            # extension clean up its own shadow tables; deleting those
            # by hand would corrupt the index.
            src_rw.execute('DROP TABLE IF EXISTS "{}"'.format(VEC_TABLE))
        src_rw.execute("COMMIT")
    except Exception:
        src_rw.execute("ROLLBACK")
        raise
    left = [r["name"] for r in master(src_rw) if r["type"] == "table"]
    log("  dropped {} moved + {} residue".format(len(move), len(drop)))
    log("  source now holds {} tables: {}".format(len(left), ", ".join(left)))

    log("")
    log("DONE. Reclaim the freed pages with VACUUM on the source when the "
        "app is still stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
