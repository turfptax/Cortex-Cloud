"""OPT-10 Phase C sub-slice 1: the People pillar lives in cortex.db.

R5 realignment: overseer_people, project_people, person_notes, and
phone_contacts are the user's relationship memory, misplaced in
overseer.db by module history. They move as ONE unit because their
FOREIGN KEYs make them inseparable (SQLite rewrites REFERENCES clauses
on rename, cross-database FKs never enforce, and delete_person relies
on ON DELETE CASCADE).

This module owns the pillar's schema and its one-time move. The
OverseerDB connection attaches cortex.db read-write as schema `userdb`
and calls ensure(); afterwards every existing unqualified statement in
overseer_db.py resolves to the moved tables (search order: main, the
read-only self attach, the gateway attach, then userdb; the originals
are renamed _migrated_* so only userdb matches).

Idempotency and crash safety: copy and verify happen for ALL tables
before ANY rename; a parity mismatch aborts with the source untouched
and the mover retries next boot. A crash between renames is healed
per-table on the next run. The _migrated_* originals are kept one
release for rollback, then dropped.
"""

from __future__ import annotations

import os
import re
import sqlite3

# Parent first: DDL creation, row copy, and renames all follow this order.
TABLES = ("overseer_people", "project_people", "person_notes",
          "phone_contacts")

# Phase C sub-slice 2 (same recipe, no FK edges): the user's own voice
# and typed journal. local_created_at is filled by application code, so
# the table carries no localizer triggers.
JOURNAL_TABLES = ("human_journal_entries",)

# Phase C sub-slice 3: the temporal narrative rollups (daily/weekly/
# monthly/yearly syntheses of the user's life). No FK edges; UNIQUE
# (kind, period_label) travels inside the table DDL; local_created_at
# is application-filled, so no triggers.
NARRATIVE_TABLES = ("temporal_narratives",)

# Phase C sub-slice 4: per-project rollups + narratives (TEXT PK, so
# parity is count-only and there is no sqlite_sequence to carry).
# Four timestamp columns carry localizer trigger pairs.
SUMMARY_TABLES = ("project_summaries",)

# Phase C sub-slice 5b, the finale: per-session gists. Its FK parents
# (raw_pointers, gist_prompts) and the vec0 vector index stay in
# overseer.db by R5; vec_gists is a STANDALONE vec0 table keyed by its
# own gist_id column (no content= binding), so the correlation is by
# stored value and survives the file boundary.
GIST_TABLES = ("summaries_gist",)

_GIST_LOCAL_PAIRS = (
    ("summaries_gist", "created_at"),
    ("summaries_gist", "axis_processed_at"),
)

GIST_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS userdb.summaries_gist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_label TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    body TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'med',
    raw_pointer_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    local_created_at TEXT NOT NULL DEFAULT '',
    prompt_version_id INTEGER NOT NULL DEFAULT 0,
    modality TEXT NOT NULL DEFAULT '',
    lens TEXT NOT NULL DEFAULT '',
    axis_processed_at TEXT,
    local_axis_processed_at TEXT DEFAULT '',
    project_tag TEXT NOT NULL DEFAULT ''
)""",
    "CREATE INDEX IF NOT EXISTS userdb.idx_gist_created"
    " ON summaries_gist(created_at)",
    "CREATE INDEX IF NOT EXISTS userdb.idx_gist_period"
    " ON summaries_gist(period_label)",
)

_SUMMARY_LOCAL_PAIRS = (
    ("project_summaries", "first_active_at"),
    ("project_summaries", "last_active_at"),
    ("project_summaries", "narrative_updated_at"),
    ("project_summaries", "stats_updated_at"),
)

SUMMARY_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS userdb.project_summaries (
    project TEXT PRIMARY KEY,
    session_count INTEGER NOT NULL DEFAULT 0,
    total_messages INTEGER NOT NULL DEFAULT 0,
    total_user_messages INTEGER NOT NULL DEFAULT 0,
    total_assistant_messages INTEGER NOT NULL DEFAULT 0,
    tool_use_message_count INTEGER NOT NULL DEFAULT 0,
    total_minutes INTEGER NOT NULL DEFAULT 0,
    avg_minutes_per_session REAL NOT NULL DEFAULT 0,
    median_minutes_per_session REAL NOT NULL DEFAULT 0,
    total_tokens_input INTEGER NOT NULL DEFAULT 0,
    total_tokens_output INTEGER NOT NULL DEFAULT 0,
    total_tokens_cache_creation INTEGER NOT NULL DEFAULT 0,
    total_tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    cost_usd_estimate REAL NOT NULL DEFAULT 0,
    cost_known_complete INTEGER NOT NULL DEFAULT 1,
    first_active_at TEXT,
    last_active_at TEXT,
    days_active_30 INTEGER NOT NULL DEFAULT 0,
    days_active_90 INTEGER NOT NULL DEFAULT 0,
    days_active_lifespan INTEGER NOT NULL DEFAULT 0,
    top_files_json TEXT NOT NULL DEFAULT '[]',
    models_used_json TEXT NOT NULL DEFAULT '{}',
    narrative TEXT NOT NULL DEFAULT '',
    narrative_updated_at TEXT,
    narrative_session_count_at_update INTEGER NOT NULL DEFAULT 0,
    stats_updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    active_minutes_total INTEGER NOT NULL DEFAULT 0,
    avg_active_minutes_per_session REAL NOT NULL DEFAULT 0,
    median_active_minutes_per_session REAL NOT NULL DEFAULT 0,
    narrative_cost_usd REAL NOT NULL DEFAULT 0,
    local_first_active_at TEXT DEFAULT '',
    local_last_active_at TEXT DEFAULT '',
    local_narrative_updated_at TEXT DEFAULT '',
    local_stats_updated_at TEXT DEFAULT '',
    child_fingerprint TEXT NOT NULL DEFAULT '',
    narrative_stale INTEGER NOT NULL DEFAULT 0,
    narrative_prompt_version_id INTEGER NOT NULL DEFAULT 0
)""",
    "CREATE INDEX IF NOT EXISTS userdb.idx_project_summaries_last_active"
    " ON project_summaries(last_active_at)",
)

NARRATIVE_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS userdb.temporal_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period_label TEXT NOT NULL,
    narrative TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    triggered_by TEXT NOT NULL DEFAULT 'loop',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    local_created_at TEXT NOT NULL DEFAULT '',
    UNIQUE(kind, period_label)
)""",
    "CREATE INDEX IF NOT EXISTS userdb.idx_temporal_kind_label"
    " ON temporal_narratives(kind, period_label)",
    "CREATE INDEX IF NOT EXISTS userdb.idx_temporal_created"
    " ON temporal_narratives(created_at)",
)

JOURNAL_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS userdb.human_journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'free',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    local_created_at TEXT NOT NULL DEFAULT ''
)""",
    "CREATE INDEX IF NOT EXISTS userdb.idx_human_journal_created"
    " ON human_journal_entries(created_at)",
)

# Fresh-install DDL: the FULL live shape (declared columns + the
# script-added merged_into_id / aliases_json / is_provisional + the
# localizer's local_* columns), captured from the live corpus
# 2026-07-27. Existing installs never use these: the mover copies the
# live sqlite_master SQL verbatim instead.
FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS userdb.overseer_people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    online_handles_json TEXT NOT NULL DEFAULT '[]',
    social_links_json TEXT NOT NULL DEFAULT '[]',
    areas_of_expertise_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_interacted_at TEXT,
    created_by_agent TEXT NOT NULL DEFAULT '',
    created_by_session_id TEXT NOT NULL DEFAULT '',
    is_provisional INTEGER NOT NULL DEFAULT 0,
    local_created_at TEXT DEFAULT '',
    local_updated_at TEXT DEFAULT '',
    local_last_interacted_at TEXT DEFAULT '',
    merged_into_id INTEGER,
    aliases_json TEXT NOT NULL DEFAULT '[]'
)""",
    """CREATE TABLE IF NOT EXISTS userdb.project_people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    person_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by_agent TEXT NOT NULL DEFAULT '',
    local_created_at TEXT DEFAULT '',
    UNIQUE(project, person_id),
    FOREIGN KEY (person_id) REFERENCES overseer_people(id) ON DELETE CASCADE
)""",
    """CREATE TABLE IF NOT EXISTS userdb.person_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'overseer',
    modality TEXT NOT NULL DEFAULT 'statement',
    note_kind TEXT NOT NULL DEFAULT 'context',
    superseded_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    local_created_at TEXT,
    created_by_agent TEXT NOT NULL DEFAULT '',
    created_by_session_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (person_id) REFERENCES overseer_people(id) ON DELETE CASCADE
)""",
    """CREATE TABLE IF NOT EXISTS userdb.phone_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT NOT NULL UNIQUE,
    display_name TEXT,
    person_id INTEGER,
    is_provisional INTEGER NOT NULL DEFAULT 1,
    call_count INTEGER NOT NULL DEFAULT 0,
    outgoing_count INTEGER NOT NULL DEFAULT 0,
    incoming_count INTEGER NOT NULL DEFAULT 0,
    total_minutes INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    imported_by_agent TEXT,
    local_created_at TEXT DEFAULT '',
    local_updated_at TEXT DEFAULT '',
    FOREIGN KEY (person_id) REFERENCES overseer_people(id)
)""",
    "CREATE INDEX IF NOT EXISTS userdb.idx_overseer_people_name_lower"
    " ON overseer_people(LOWER(name))",
    "CREATE INDEX IF NOT EXISTS userdb.idx_overseer_people_created"
    " ON overseer_people(created_at)",
    "CREATE INDEX IF NOT EXISTS userdb.idx_project_people_project"
    " ON project_people(project)",
    "CREATE INDEX IF NOT EXISTS userdb.idx_project_people_person"
    " ON project_people(person_id)",
    "CREATE INDEX IF NOT EXISTS userdb.idx_person_notes_person"
    " ON person_notes(person_id)",
    "CREATE INDEX IF NOT EXISTS userdb.idx_person_notes_live"
    " ON person_notes(person_id, superseded_by)",
    "CREATE INDEX IF NOT EXISTS userdb.phone_contacts_count"
    " ON phone_contacts(call_count)",
)

# (table, timestamp column) pairs that carry a paired local_ column,
# per the locked always-local-with-tz rule.
_LOCAL_PAIRS = (
    ("overseer_people", "created_at"),
    ("overseer_people", "updated_at"),
    ("overseer_people", "last_interacted_at"),
    ("project_people", "created_at"),
    ("person_notes", "created_at"),
    ("phone_contacts", "created_at"),
    ("phone_contacts", "updated_at"),
)

_LOCAL_EXPR = (
    "strftime('%Y-%m-%dT%H:%M:%S', NEW.{c}, 'localtime') || "
    "printf('%+03d:%02d', (CAST(strftime('%s', NEW.{c}, 'localtime') AS "
    "INTEGER) - CAST(strftime('%s', NEW.{c}) AS INTEGER)) / 3600, "
    "ABS((CAST(strftime('%s', NEW.{c}, 'localtime') AS INTEGER) - "
    "CAST(strftime('%s', NEW.{c}) AS INTEGER)) / 60 % 60))"
)


def _fresh_triggers(pairs=_LOCAL_PAIRS):
    """Localizer-equivalent triggers for the fresh-install path. The
    moved path copies the live trigger SQL verbatim instead; these only
    need to match the localizer's behavior, not its exact text."""
    out = []
    for table, col in pairs:
        expr = _LOCAL_EXPR.format(c=col)
        out.append(
            "CREATE TRIGGER IF NOT EXISTS userdb.tgr_{t}_local_{c}_ai "
            "AFTER INSERT ON {t} WHEN NEW.{c} IS NOT NULL AND "
            "(NEW.local_{c} IS NULL OR NEW.local_{c} = '') BEGIN "
            "UPDATE {t} SET local_{c} = {e} WHERE rowid = NEW.rowid; END"
            .format(t=table, c=col, e=expr))
        out.append(
            "CREATE TRIGGER IF NOT EXISTS userdb.tgr_{t}_local_{c}_au "
            "AFTER UPDATE OF {c} ON {t} WHEN NEW.{c} IS NOT NULL AND "
            "(NEW.{c} != OLD.{c} OR OLD.{c} IS NULL) BEGIN "
            "UPDATE {t} SET local_{c} = {e} WHERE rowid = NEW.rowid; END"
            .format(t=table, c=col, e=expr))
    return out


def _has(conn, schema, table):
    row = conn.execute(
        "SELECT 1 FROM {}.sqlite_master WHERE type='table' AND name=?"
        .format(schema), (table,)).fetchone()
    return row is not None


def _retarget(sql, kind):
    """Qualify a sqlite_master CREATE statement so it executes into the
    userdb schema. The schema prefix goes on the created OBJECT name
    (table, index, or trigger); ON <table> clauses stay unqualified
    because the object must live in the same database as its table."""
    pattern = {
        "table": r"(CREATE\s+TABLE\s+)(?:IF\s+NOT\s+EXISTS\s+)?([\"\w]+)",
        "index": r"(CREATE\s+(?:UNIQUE\s+)?INDEX\s+)(?:IF\s+NOT\s+EXISTS\s+)?([\"\w]+)",
        "trigger": r"(CREATE\s+TRIGGER\s+)(?:IF\s+NOT\s+EXISTS\s+)?([\"\w]+)",
    }[kind]
    return re.sub(pattern, r"\1userdb.\2", sql, count=1, flags=re.IGNORECASE)


# Sub-slice 5b: FK parents that stay in overseer.db. A moved child
# keeping these clauses cannot be written at all: SQLite resolves FK
# parents only within the child's own database, so every INSERT raises
# "no such table: userdb.<parent>" under foreign_keys=ON. Cross-database
# FKs never enforce anyway, so the clause is dropped and the column
# becomes a soft reference (same trade as sub-slice 5a's inbound half).
STAY_BEHIND_PARENTS = ("raw_pointers", "gist_prompts", "imported_sessions",
                       "summaries_episode")

_OUTBOUND_FK_RE = re.compile(
    r",?\s*FOREIGN\s+KEY\s*\(\s*\w+\s*\)\s*REFERENCES\s+"
    r"(?:{})\s*\([^)]*\)(\s+ON\s+DELETE\s+\w+)?".format(
        "|".join(STAY_BEHIND_PARENTS)),
    re.IGNORECASE)


def _strip_stay_behind_fks(sql, log=None, table=""):
    out = _OUTBOUND_FK_RE.sub("", sql)
    if out == sql:
        return sql
    out = re.sub(r",(\s*)\)\s*$", r"\1)", out.rstrip().rstrip(";"))
    if log:
        log("people_pillar: %s copied without its FK(s) to tables that "
            "stay in overseer.db (soft references now)" % table)
    return out


def _copy_schema(conn, table, log):
    """Recreate one table (plus its indexes and triggers) in userdb by
    copying the LIVE sqlite_master SQL verbatim, so script-added columns
    and localizer triggers travel exactly as they exist. The one
    deliberate edit: FK clauses pointing at tables that stay behind are
    dropped, because they would make the moved table unwritable."""
    tsql = conn.execute(
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()[0]
    tsql = _strip_stay_behind_fks(tsql, log, table)
    conn.execute(_retarget(tsql, "table"))
    for kind in ("index", "trigger"):
        rows = conn.execute(
            "SELECT sql FROM main.sqlite_master WHERE type=? AND "
            "tbl_name=? AND sql IS NOT NULL", (kind, table)).fetchall()
        for (sql,) in rows:
            conn.execute(_retarget(sql, kind))
    if log:
        log("people_pillar: schema copied for %s" % table)


def _copy_sequence(conn, table):
    row = conn.execute(
        "SELECT seq FROM main.sqlite_sequence WHERE name=?",
        (table,)).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM userdb.sqlite_sequence WHERE name=?", (table,))
    conn.execute("INSERT INTO userdb.sqlite_sequence (name, seq) "
                 "VALUES (?, ?)", (table, row[0]))


def _parity(conn, table):
    def stats(schema):
        try:
            return tuple(conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(id), 0) FROM {}.{}"
                .format(schema, table)).fetchone())
        except sqlite3.OperationalError:
            # TEXT-PK tables (project_summaries) have no id column;
            # count parity only. rowids are not copy-stable, so they
            # are useless as a checksum here.
            return tuple(conn.execute(
                "SELECT COUNT(*), 0 FROM {}.{}"
                .format(schema, table)).fetchone())
    return stats("main"), stats("userdb")


def _retire_legacy_people(conn, log):
    """The deprecated cortex.db `people` table (V2-FIX): absorb its rows
    into overseer_people with provenance, then rename it out of every
    surface so the user's ledger has exactly one people table."""
    if not _has(conn, "userdb", "people"):
        return 0
    absorbed = 0
    for row in conn.execute("SELECT * FROM userdb.people").fetchall():
        keys = row.keys() if hasattr(row, "keys") else []
        d = {k: row[k] for k in keys} if keys else {}
        name = (d.get("name") or d.get("id") or "").strip()
        if not name:
            continue
        prov = "[legacy people import] role=%s | email=%s | projects=%s | %s" % (
            d.get("role") or "", d.get("email") or "",
            d.get("projects") or "", d.get("notes") or "")
        hit = conn.execute(
            "SELECT id, notes FROM userdb.overseer_people "
            "WHERE LOWER(name)=LOWER(?) AND merged_into_id IS NULL",
            (name,)).fetchone()
        if hit is not None:
            if prov not in (hit[1] or ""):
                conn.execute(
                    "UPDATE userdb.overseer_people SET notes = notes || "
                    "char(10) || ?, updated_at = datetime('now') "
                    "WHERE id = ?", (prov, hit[0]))
                absorbed += 1
        else:
            conn.execute(
                "INSERT INTO userdb.overseer_people (name, notes, "
                "created_by_agent) VALUES (?, ?, 'legacy-people-migration')",
                (name, prov))
            absorbed += 1
    conn.execute("ALTER TABLE userdb.people RENAME TO _migrated_people")
    if log:
        log("people_pillar: legacy people retired (%d rows absorbed)"
            % absorbed)
    return absorbed


def _count(conn, schema, table):
    return conn.execute(
        "SELECT COUNT(*) FROM {}.{}".format(schema, table)).fetchone()[0]


# ── Phase C sub-slice 5a: FK detachment ───────────────────────────────
# summaries_gist is the first table whose FOREIGN KEYs cross the move
# boundary in BOTH directions. These three bookkeeping tables stay in
# overseer.db (AI process state, R5) but reference summaries_gist, and
# ALTER TABLE RENAME rewrites a child's REFERENCES clause in place: the
# moment the gist table is renamed away, their inserts fail against a
# frozen archive, and they dangle when it is dropped. Detaching the
# edge first is its own reversible step: cross-database FKs never
# enforce anyway, so gist_id becomes a soft reference exactly like
# every other cross-ledger id in the system.
FK_CHILDREN = ("processed_sessions", "processed_imported_sessions",
               "automation_rollups")

_GIST_FK_RE = re.compile(
    r",?\s*FOREIGN\s+KEY\s*\(\s*gist_id\s*\)\s*REFERENCES\s+"
    r"summaries_gist\s*\([^)]*\)(\s+ON\s+DELETE\s+\w+)?",
    re.IGNORECASE)


def _strip_gist_fk(sql):
    """Remove the summaries_gist FK clause, leaving every other clause
    (columns, other FOREIGN KEYs, UNIQUE) byte-identical. Returns None
    when there is nothing to strip."""
    out = _GIST_FK_RE.sub("", sql, count=1)
    if out == sql:
        return None
    # A stripped trailing clause can leave "...,\n)" behind.
    return re.sub(r",(\s*)\)\s*$", r"\1)", out.rstrip().rstrip(";"))


def detach_gist_fks(conn, log=None):
    """Rebuild each FK child without its summaries_gist edge, using
    SQLite's documented table-rebuild procedure. Idempotent (a table
    whose SQL no longer carries the clause is skipped), parity-gated,
    and each table is its own transaction so a crash re-runs cleanly."""
    report = {"detached": [], "skipped": []}
    for table in FK_CHILDREN:
        row = conn.execute(
            "SELECT sql FROM main.sqlite_master WHERE type='table' "
            "AND name=?", (table,)).fetchone()
        if row is None:
            report["skipped"].append(table)
            continue
        stripped = _strip_gist_fk(row[0])
        if stripped is None:
            report["skipped"].append(table)
            continue
        extras = [r[0] for r in conn.execute(
            "SELECT sql FROM main.sqlite_master WHERE type IN "
            "('index', 'trigger') AND tbl_name=? AND sql IS NOT NULL",
            (table,)).fetchall()]
        before = _count(conn, "main", table)
        tmp = "_fkdetach_" + table
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(re.sub(
                r"(CREATE\s+TABLE\s+)([\"\w]+)", r"\1" + tmp, stripped,
                count=1, flags=re.IGNORECASE))
            conn.execute("INSERT INTO {} SELECT * FROM {}".format(tmp, table))
            after = conn.execute(
                "SELECT COUNT(*) FROM {}".format(tmp)).fetchone()[0]
            if after != before:
                conn.rollback()
                if log:
                    log("fk_detach: %s parity %d != %d, aborted"
                        % (table, before, after))
                report.setdefault("failed", []).append(table)
                continue
            conn.execute("DROP TABLE {}".format(table))
            conn.execute("ALTER TABLE {} RENAME TO {}".format(tmp, table))
            for sql in extras:
                conn.execute(sql)
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                conn.rollback()
                if log:
                    log("fk_detach: %s foreign_key_check dirty, aborted"
                        % table)
                report.setdefault("failed", []).append(table)
                continue
            conn.commit()
            report["detached"].append(table)
            if log:
                log("fk_detach: %s rebuilt without the summaries_gist FK "
                    "(%d rows)" % (table, before))
        except Exception as e:
            conn.rollback()
            if log:
                log("fk_detach: %s failed (%s); table untouched" % (table, e))
            report.setdefault("failed", []).append(table)
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
    return report


def _move_group(conn, tables, fresh_sqls, log, label):
    """The hardened move recipe for one table group.

    Transaction discipline (adversarial review 2026-07-27): WAL makes
    cross-file commits non-atomic, so the move is TWO explicit
    single-purpose transactions ordered copies-first. Phase 1 re-syncs
    the userdb copies from main (source-authoritative: DELETE children
    first, then INSERT fresh; a stale or partial prior copy is healed,
    never trusted) and commits, with FK enforcement off so pre-FK
    orphan rows in live data copy verbatim. Phase 2 renames the main
    originals in ONE transaction (single file = atomic under WAL).
    Every crash point re-runs cleanly: before phase-1 commit nothing
    persisted; between the commits, main still owns the tables and the
    next boot re-syncs; after phase 2, done. Aborts roll back inside
    their own transaction, so no schema residue can shadow live data
    on other connections."""
    report = {"state": "already-moved"}
    in_main = [t for t in tables if _has(conn, "main", t)]

    # Rollback healing: a previous-image boot re-executes the old
    # OVERSEER_SCHEMA_SQL and recreates EMPTY tables in main, which
    # would shadow the moved data forever. If main's copy is empty,
    # userdb's is populated, and the _migrated_ original is present,
    # the empty shadow is that artifact: drop it.
    healed = []
    for t in list(in_main):
        try:
            if (_count(conn, "main", t) == 0
                    and _has(conn, "userdb", t)
                    and _count(conn, "userdb", t) > 0
                    and _has(conn, "main", "_migrated_" + t)):
                conn.execute("DROP TABLE main.{}".format(t))
                conn.commit()
                in_main.remove(t)
                healed.append(t)
        except Exception:
            pass
    if healed:
        report["healed"] = healed
        if log:
            log("%s: dropped empty rollback shadows %s" % (label, healed))

    if in_main:
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for t in [x for x in reversed(tables) if x in in_main]:
                if _has(conn, "userdb", t):
                    conn.execute("DELETE FROM userdb.{}".format(t))
            for t in in_main:
                if not _has(conn, "userdb", t):
                    _copy_schema(conn, t, log)
                conn.execute(
                    "INSERT INTO userdb.{t} SELECT * FROM main.{t}"
                    .format(t=t))
                _copy_sequence(conn, t)
            bad = []
            for t in in_main:
                src, dst = _parity(conn, t)
                if src != dst:
                    bad.append((t, src, dst))
            if bad:
                conn.rollback()
                if log:
                    log("%s: PARITY MISMATCH %r; move aborted with zero "
                        "residue, will retry next boot" % (label, bad))
                report.update(state="parity-failed", detail=bad)
                return report
            conn.commit()
        except Exception as e:
            conn.rollback()
            if log:
                log("%s: copy failed (%s); move aborted with zero "
                    "residue, will retry next boot" % (label, e))
            report.update(state="parity-failed", detail=str(e)[:200])
            return report
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for t in in_main:  # parent first by the group's order
                conn.execute(
                    "ALTER TABLE main.{t} RENAME TO _migrated_{t}"
                    .format(t=t))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if log:
                log("%s: rename failed (%s); copies are durable, "
                    "renames retry next boot" % (label, e))
            report.update(state="rename-failed", detail=str(e)[:200])
            return report
        report.update(state="moved", tables=in_main)
        if log:
            log("%s: moved %s into cortex.db" % (label, in_main))
    elif not all(_has(conn, "userdb", t) for t in tables):
        # Fresh install (or resuming a crashed fresh creation: every
        # fresh statement is IF NOT EXISTS).
        try:
            conn.execute("BEGIN IMMEDIATE")
            for sql in fresh_sqls:
                conn.execute(sql)
            conn.commit()
        except Exception as e:
            conn.rollback()
            report.update(state="fresh-failed", detail=str(e)[:200])
            return report
        report["state"] = "fresh"
        if log:
            log("%s: fresh install, tables created in cortex.db" % label)
    return report


def ensure(conn, log=None):
    """Entry point, called by OverseerDB after the userdb attach. Moves
    each group with the hardened recipe, creates them fresh where there
    is nothing to move, and retires the legacy people table. The report
    keeps the people group's fields at the top level (established
    consumers) with the journal group nested."""
    report = dict(_move_group(conn, TABLES,
                              list(FRESH_DDL) + _fresh_triggers(),
                              log, "people_pillar"))
    report["journal"] = _move_group(conn, JOURNAL_TABLES,
                                    list(JOURNAL_FRESH_DDL),
                                    log, "journal_move")
    report["narratives"] = _move_group(conn, NARRATIVE_TABLES,
                                       list(NARRATIVE_FRESH_DDL),
                                       log, "narratives_move")
    report["summaries"] = _move_group(
        conn, SUMMARY_TABLES,
        list(SUMMARY_FRESH_DDL) + _fresh_triggers(_SUMMARY_LOCAL_PAIRS),
        log, "summaries_move")
    # Sub-slice 5a: detach the gist FK edges so 5b can move the table
    # without freezing these children against a renamed archive. Must
    # run BEFORE the gist move on the same boot.
    report["fk_detach"] = detach_gist_fks(conn, log)
    report["gists"] = _move_group(
        conn, GIST_TABLES,
        list(GIST_FRESH_DDL) + _fresh_triggers(_GIST_LOCAL_PAIRS),
        log, "gists_move")
    # Legacy retirement is sealed separately: a failure here reports
    # but never unwinds the completed moves.
    report["absorbed"] = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        report["absorbed"] = _retire_legacy_people(conn, log)
        conn.commit()
    except Exception as e:
        conn.rollback()
        report["absorb_error"] = str(e)[:200]
    return report
