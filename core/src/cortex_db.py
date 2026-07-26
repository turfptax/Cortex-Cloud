"""Cortex Core - SQLite persistence layer.

Manages the Cortex knowledge database (cortex.db). Core tables:
sessions, notes, activities, searches, projects, organizations,
time_entries, computers, people, files, training_examples, training_ledger.

Pet/heartbeat schema and helpers live in the cortex-pet sister repo's
pet_db.py (Slice 2c2d schema move; Slice 11 full plugin extraction).
The pet plugin uses PetDB(pet.db); core uses CortexDB(cortex.db).
Plugin is loaded at runtime on production .25 from the sibling repo.
See https://github.com/turfptax/cortex-pet

Uses WAL mode for safe concurrent reads during writes.
All timestamps are ISO 8601 UTC via SQLite datetime('now').
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone


def _utcnow_iso():
    """UTC timestamp in the same 'YYYY-MM-DD HH:MM:SS' shape SQLite's
    datetime('now') produces, so task rows sort uniformly."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    ai_platform TEXT DEFAULT '',
    hostname TEXT DEFAULT '',
    os_info TEXT DEFAULT '',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    summary TEXT DEFAULT '',
    projects TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    project TEXT DEFAULT '',
    note_type TEXT DEFAULT 'note',
    source TEXT DEFAULT 'ble',
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program TEXT NOT NULL,
    details TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    project TEXT DEFAULT '',
    session_id TEXT,
    duration_min INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    source TEXT DEFAULT '',
    url TEXT DEFAULT '',
    project TEXT DEFAULT '',
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS projects (
    tag TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    priority INTEGER DEFAULT 3,
    description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    org_tag TEXT DEFAULT '',
    github_url TEXT DEFAULT '',
    total_hours REAL DEFAULT 0,
    collaborators TEXT DEFAULT '',
    last_touched TEXT DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_aliases (
    alias        TEXT PRIMARY KEY,   -- observed name: cwd basename, merged-away tag, phone-local name
    project_tag  TEXT NOT NULL,      -- canonical projects.tag
    source       TEXT DEFAULT '',    -- 'auto-exact' | 'auto-normalized' | 'overseer-proposed' | 'tory'
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid         TEXT UNIQUE NOT NULL,       -- idempotency: sync push, extraction dedup, agent retries
    project_tag  TEXT NOT NULL,              -- validated at the write contract; tasks always live under a project
    title        TEXT NOT NULL,
    details      TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open',
                 -- open | in_progress | blocked | done | cancelled  (business-tracker parity; FEDERATE posture)
    priority     INTEGER DEFAULT 3,          -- same 1-5 scale as projects.priority
    due_date     TEXT DEFAULT '',            -- local date, per the local-with-tz rule
    proposed     INTEGER NOT NULL DEFAULT 0, -- 1 = overseer-extracted, awaiting accept; excluded from open counts
    source       TEXT NOT NULL DEFAULT 'manual',
                 -- 'manual' | 'overseer-extracted' | 'reminder-migrated' | 'agent'
    source_ref   TEXT DEFAULT '',            -- provenance: gist id, session id, note id
    created_by   TEXT DEFAULT '',            -- connector handle / 'tory' / 'overseer'
    external_ref TEXT DEFAULT '',            -- business-tracker task id hedge (federate link)
    completed_at TEXT DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_tag, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due     ON tasks(status, due_date);

CREATE TABLE IF NOT EXISTS organizations (
    tag TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    org_type TEXT DEFAULT '',
    my_role TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_tag TEXT DEFAULT '',
    org_tag TEXT DEFAULT '',
    activity_type TEXT DEFAULT '',
    description TEXT DEFAULT '',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    duration_minutes INTEGER DEFAULT 0,
    source TEXT DEFAULT 'import',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_time_project ON time_entries(project_tag);
CREATE INDEX IF NOT EXISTS idx_time_started ON time_entries(started_at);
CREATE INDEX IF NOT EXISTS idx_time_org ON time_entries(org_tag);

CREATE TABLE IF NOT EXISTS computers (
    hostname TEXT PRIMARY KEY,
    os TEXT DEFAULT '',
    cpu TEXT DEFAULT '',
    gpu TEXT DEFAULT '',
    ram_gb REAL DEFAULT 0,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT DEFAULT (datetime('now')),
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    role TEXT DEFAULT '',
    email TEXT DEFAULT '',
    projects TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    category TEXT DEFAULT 'uploads',
    description TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    project TEXT DEFAULT '',
    mime_type TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    source TEXT DEFAULT 'upload',
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at);
CREATE INDEX IF NOT EXISTS idx_notes_session ON notes(session_id);
CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(note_type);
CREATE INDEX IF NOT EXISTS idx_activities_project ON activities(project);
CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at);
CREATE INDEX IF NOT EXISTS idx_searches_project ON searches(project);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(ended_at);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at);

-- ── Training Examples (generated by learn cycles) ───────────
CREATE TABLE IF NOT EXISTS training_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    messages TEXT NOT NULL,
    source TEXT DEFAULT 'learn-cycle',
    source_id TEXT DEFAULT '',
    cycle_id INTEGER DEFAULT 0,
    model TEXT DEFAULT '',
    server TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_training_examples_cycle ON training_examples(cycle_id);
CREATE INDEX IF NOT EXISTS idx_training_examples_source ON training_examples(source);

-- ── Training Ledger (tracks learn cycle state) ──────────────
CREATE TABLE IF NOT EXISTS training_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class CortexDB:
    """SQLite persistence layer for the Cortex wearable knowledge system."""

    def __init__(self, db_path):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        self._migrate_opt2_orgs()
        self._conn.commit()
        # Slice 9.4.1 (2026-05-16): every timestamp column gets a
        # paired local_<col>_at (ISO with explicit offset) populated
        # by trigger. Backstops the durable rule that every displayed
        # time must include timezone - see
        # memory/feedback_time_always_local_with_tz.md.
        # Idempotent: cost-near-zero after first call.
        from timestamp_localizer import ensure_local_timestamp_columns
        ensure_local_timestamp_columns(self._conn)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Notes ---

    def insert_note(self, content, tags="", project="", note_type="note",
                    source="ble", session_id=None):
        cur = self._conn.execute(
            "INSERT INTO notes (content, tags, project, note_type, source, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (content, tags, project, note_type, source, session_id),
        )
        self._conn.commit()
        return cur.lastrowid

    # --- Activities ---

    def insert_activity(self, program, details="", file_path="",
                        project="", session_id=None, duration_min=0):
        cur = self._conn.execute(
            "INSERT INTO activities (program, details, file_path, project, "
            "session_id, duration_min) VALUES (?, ?, ?, ?, ?, ?)",
            (program, details, file_path, project, session_id, duration_min),
        )
        self._conn.commit()
        return cur.lastrowid

    # --- Searches ---

    def insert_search(self, query, source="", url="", project="",
                      session_id=None):
        cur = self._conn.execute(
            "INSERT INTO searches (query, source, url, project, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (query, source, url, project, session_id),
        )
        self._conn.commit()
        return cur.lastrowid

    # --- Sessions ---

    def start_session(self, ai_platform="", hostname="", os_info=""):
        session_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO sessions (id, ai_platform, hostname, os_info) "
            "VALUES (?, ?, ?, ?)",
            (session_id, ai_platform, hostname, os_info),
        )
        # Upsert computer record
        if hostname:
            self._conn.execute(
                "INSERT INTO computers (hostname, os) VALUES (?, ?) "
                "ON CONFLICT(hostname) DO UPDATE SET os=excluded.os, "
                "last_seen=datetime('now')",
                (hostname, os_info),
            )
        self._conn.commit()
        return session_id

    def end_session(self, session_id, summary="", projects=""):
        cur = self._conn.execute(
            "UPDATE sessions SET ended_at=datetime('now'), summary=?, projects=? "
            "WHERE id=? AND ended_at IS NULL",
            (summary, projects, session_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # --- Projects ---

    def upsert_project(self, tag, name="", status="active", priority=3,
                       description="", category="", org_tag="",
                       github_url="", total_hours=0, collaborators=""):
        self._conn.execute(
            "INSERT INTO projects (tag, name, status, priority, description, "
            "category, org_tag, github_url, total_hours, collaborators) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tag) DO UPDATE SET name=excluded.name, "
            "status=excluded.status, priority=excluded.priority, "
            "description=excluded.description, "
            "category=excluded.category, org_tag=excluded.org_tag, "
            "github_url=excluded.github_url, total_hours=excluded.total_hours, "
            "collaborators=excluded.collaborators, "
            "last_touched=datetime('now')",
            (tag, name, status, priority, description, category,
             org_tag, github_url, total_hours, collaborators),
        )
        self._conn.commit()
        return tag

    def get_project_by_tag(self, tag):
        """Slice 9.6 CP2: lookup single project row by tag, returns
        dict or None. Used by overseer's update_project_status tool
        to preserve existing name/description on partial updates."""
        row = self._conn.execute(
            "SELECT * FROM projects WHERE tag = ?", (tag,)
        ).fetchone()
        return dict(row) if row else None

    def update_project_status_only(self, tag, status):
        """Slice 9.6 CP2: change status without touching other fields.
        Returns True if a row was updated, False if no project with
        that tag exists."""
        cur = self._conn.execute(
            "UPDATE projects SET status = ?, last_touched = datetime('now') "
            "WHERE tag = ?",
            (status, tag),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # --- Organizations ---

    # upsert_org was removed in OPT-2 (2026-07-26): zero callers, and org
    # writes go through the generic upsert_row contract like every other
    # whitelisted table.

    def _migrate_opt2_orgs(self):
        """OPT-2: wake the dormant org layer. Adds the default-bucket,
        business-tracker-hedge, and ordering columns (CREATE TABLE IF NOT
        EXISTS never retrofits), then guarantees the 'unsorted' bucket
        row. org_tag='' stays UNTRIAGED (nobody decided); 'unsorted'
        means TRIAGED, deliberately parked."""
        for col, decl in (("is_default", "INTEGER DEFAULT 0"),
                          ("external_ref", "TEXT DEFAULT ''"),
                          ("sort_order", "INTEGER DEFAULT 0")):
            try:
                self._conn.execute(
                    "ALTER TABLE organizations ADD COLUMN {} {}".format(
                        col, decl))
            except Exception:
                pass  # column already exists
        self._conn.execute(
            "INSERT OR IGNORE INTO organizations "
            "(tag, name, org_type, is_default, sort_order) "
            "VALUES ('unsorted', 'Unsorted', 'thematic', 1, 999)")
        self._conn.commit()

    def valid_org_tags(self):
        return [r[0] for r in self._conn.execute(
            "SELECT tag FROM organizations ORDER BY sort_order, tag")]

    def get_organizations(self):
        rows = self._conn.execute(
            "SELECT * FROM organizations ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Time Entries ---

    def insert_time_entry(self, project_tag="", org_tag="", activity_type="",
                          description="", started_at=None, duration_minutes=0,
                          source="manual"):
        cur = self._conn.execute(
            "INSERT INTO time_entries (project_tag, org_tag, activity_type, "
            "description, started_at, duration_minutes, source) "
            "VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?)",
            (project_tag, org_tag, activity_type, description,
             started_at, duration_minutes, source),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_project_hours(self, project_tag):
        """Recalculate total_hours for a project from its time_entries."""
        self._conn.execute(
            "UPDATE projects SET total_hours = COALESCE("
            "  (SELECT ROUND(SUM(duration_minutes)/60.0, 1) "
            "   FROM time_entries WHERE project_tag = ?), 0"
            "), last_touched = datetime('now') WHERE tag = ?",
            (project_tag, project_tag),
        )
        self._conn.commit()

    def project_exists(self, tag):
        """Check if a project tag exists."""
        row = self._conn.execute(
            "SELECT 1 FROM projects WHERE tag = ?", (tag,)
        ).fetchone()
        return row is not None

    def get_time_summary(self, project_tag=None, limit=20):
        """Get time summary grouped by project."""
        if project_tag:
            rows = self._conn.execute(
                "SELECT project_tag, activity_type, "
                "SUM(duration_minutes) AS total_min, "
                "ROUND(SUM(duration_minutes)/60.0, 1) AS total_hours, "
                "COUNT(*) AS entries "
                "FROM time_entries WHERE project_tag = ? "
                "GROUP BY activity_type ORDER BY total_min DESC",
                (project_tag,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT project_tag, "
                "SUM(duration_minutes) AS total_min, "
                "ROUND(SUM(duration_minutes)/60.0, 1) AS total_hours, "
                "COUNT(*) AS entries "
                "FROM time_entries "
                "GROUP BY project_tag ORDER BY total_min DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Computers ---

    def register_computer(self, hostname, os="", cpu="", gpu="", ram_gb=0,
                          notes=""):
        self._conn.execute(
            "INSERT INTO computers (hostname, os, cpu, gpu, ram_gb, notes) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(hostname) DO UPDATE SET os=excluded.os, "
            "cpu=excluded.cpu, gpu=excluded.gpu, ram_gb=excluded.ram_gb, "
            "notes=excluded.notes, last_seen=datetime('now')",
            (hostname, os, cpu, gpu, ram_gb, notes),
        )
        self._conn.commit()
        return hostname

    # --- Files ---

    def insert_file(self, filename, category="uploads", description="",
                    tags="", project="", mime_type="", size_bytes=0,
                    source="upload", session_id=None):
        cur = self._conn.execute(
            "INSERT INTO files (filename, category, description, tags, project, "
            "mime_type, size_bytes, source, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (filename, category, description, tags, project,
             mime_type, size_bytes, source, session_id),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_files(self, category=None, project=None, limit=50):
        sql = "SELECT * FROM files"
        params = []
        wheres = []
        if category:
            wheres.append("category = ?")
            params.append(category)
        if project:
            wheres.append("project = ?")
            params.append(project)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_files(self, query, limit=20):
        rows = self._conn.execute(
            "SELECT * FROM files WHERE filename LIKE ? OR description LIKE ? "
            "OR tags LIKE ? ORDER BY created_at DESC LIMIT ?",
            ("%{}%".format(query), "%{}%".format(query),
             "%{}%".format(query), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_file(self, file_id):
        cur = self._conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- People ---

    def upsert_person(self, person_id, name="", role="", email="",
                      projects="", notes=""):
        self._conn.execute(
            "INSERT INTO people (id, name, role, email, projects, notes) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "role=excluded.role, email=excluded.email, "
            "projects=excluded.projects, notes=excluded.notes",
            (person_id, name, role, email, projects, notes),
        )
        self._conn.commit()
        return person_id

    # --- Stats / Queries ---

    def get_stats(self):
        row = self._conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM notes) AS notes_total, "
            "(SELECT COUNT(*) FROM activities) AS activities_total, "
            "(SELECT COUNT(*) FROM searches) AS searches_total, "
            "(SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL) AS active_sessions, "
            "(SELECT COUNT(*) FROM sessions) AS sessions_total, "
            "(SELECT COUNT(*) FROM projects) AS projects_total, "
            "(SELECT COUNT(*) FROM files) AS files_total, "
            "(SELECT COUNT(*) FROM organizations) AS orgs_total, "
            "(SELECT COUNT(*) FROM time_entries) AS time_entries_total, "
            "(SELECT ROUND(SUM(duration_minutes)/60.0, 1) FROM time_entries) AS total_hours_tracked"
        ).fetchone()
        return dict(row)

    def get_recent_notes(self, limit=10, project=None, note_type=None):
        sql = "SELECT * FROM notes"
        params = []
        wheres = []
        if project:
            wheres.append("project = ?")
            params.append(project)
        if note_type:
            wheres.append("note_type = ?")
            params.append(note_type)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_recent_sessions(self, limit=5):
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_active_projects(self):
        rows = self._conn.execute(
            "SELECT * FROM projects WHERE status='active' "
            "ORDER BY last_touched DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Generic CRUD ---

    # Tables that accept writes via the generic upsert/delete commands.
    WRITABLE_TABLES = {
        "notes":              {"pk": "id",  "auto_pk": True},
        "projects":           {"pk": "tag", "auto_pk": False},
        "organizations":      {"pk": "tag", "auto_pk": False},
        "tasks":              {"pk": "id",  "auto_pk": True},
        "project_aliases":    {"pk": "alias", "auto_pk": False},
        "time_entries":       {"pk": "id",  "auto_pk": True},
        "people":             {"pk": "id",  "auto_pk": False},
        "computers":          {"pk": "hostname", "auto_pk": False},
        "training_examples":  {"pk": "id",  "auto_pk": True},
        "training_ledger":    {"pk": "id",  "auto_pk": True},
    }

    # ── OPT-3: task write contract ────────────────────────────────

    TASK_STATUSES = ("open", "in_progress", "blocked", "done", "cancelled")

    def _prep_task_write(self, data):
        """Guards + lifecycle stamps for the tasks table (applied inside
        upsert_row, the single write path). Creates require title +
        project_tag; project_tag resolves through the alias map and must
        name a real project. uuid gives idempotency: a write carrying an
        existing uuid becomes an update of that row. Status uses the
        business-tracker parity vocabulary; done/cancelled stamp
        completed_at; every write bumps updated_at."""
        if data.get("status") and data["status"] not in self.TASK_STATUSES:
            raise ValueError(
                "invalid status '{}'; valid: {}".format(
                    data["status"], " | ".join(self.TASK_STATUSES)))
        # uuid idempotency: an existing uuid routes to that row.
        if "id" not in data and data.get("uuid"):
            hit = self._conn.execute(
                "SELECT id FROM tasks WHERE uuid = ?",
                (data["uuid"],)).fetchone()
            if hit:
                data["id"] = hit[0]
        creating = "id" not in data
        if creating:
            if not (data.get("title") or "").strip():
                raise ValueError("task title is required")
            ptag = (data.get("project_tag") or "").strip()
            if not ptag:
                raise ValueError("project_tag is required: tasks always "
                                 "live under a project")
            canonical = self.resolve_project_tag(ptag)
            exists = self._conn.execute(
                "SELECT 1 FROM projects WHERE tag = ?",
                (canonical,)).fetchone()
            if not exists:
                raise ValueError(
                    "unknown project '{}'; create the project first or "
                    "use a known tag".format(ptag))
            data["project_tag"] = canonical
            data.setdefault("uuid", str(uuid.uuid4()))
        else:
            # Partial update: never let the key fields be blanked, and
            # re-resolve a supplied project_tag.
            if "project_tag" in data and data["project_tag"]:
                data["project_tag"] = self.resolve_project_tag(
                    data["project_tag"])
        if data.get("status") in ("done", "cancelled") \
                and not data.get("completed_at"):
            data["completed_at"] = _utcnow_iso()
        data["updated_at"] = _utcnow_iso()
        return data

    def open_tasks(self, *, limit=10):
        """Real (non-proposed) tasks still in play, for get_context and
        the working-memory glance. Priority 1 first, then nearest due."""
        rows = self._conn.execute(
            "SELECT id, uuid, project_tag, title, status, priority, "
            "due_date, created_by FROM tasks "
            "WHERE proposed = 0 AND status IN ('open','in_progress','blocked') "
            "ORDER BY priority ASC, CASE WHEN due_date = '' THEN 1 ELSE 0 END, "
            "due_date ASC LIMIT ?", (int(limit),)).fetchall()
        count = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE proposed = 0 "
            "AND status IN ('open','in_progress','blocked')").fetchone()[0]
        return {"count": count,
                "top": [dict(zip((
                    "id", "uuid", "project_tag", "title", "status",
                    "priority", "due_date", "created_by"), r)) for r in rows]}

    def migrate_reminders_to_tasks(self):
        """One-time OPT-3 migration: reminder notes become tasks
        (source='reminder-migrated'; the original notes are KEPT as
        provenance). Idempotent via uuid 'rem-<note_id>'. Reminders on
        unknown projects land under their resolved tag when it exists,
        else are skipped and reported (never auto-create projects)."""
        notes = self._conn.execute(
            "SELECT id, content, project, created_at FROM notes "
            "WHERE note_type = 'reminder' ORDER BY id").fetchall()
        migrated = skipped_no_project = already = 0
        skipped = []
        for nid, content, project, created_at in notes:
            nuuid = "rem-{}".format(nid)
            if self._conn.execute("SELECT 1 FROM tasks WHERE uuid = ?",
                                  (nuuid,)).fetchone():
                already += 1
                continue
            canonical = self.resolve_project_tag((project or "").strip())
            if not canonical or not self._conn.execute(
                    "SELECT 1 FROM projects WHERE tag = ?",
                    (canonical,)).fetchone():
                skipped_no_project += 1
                skipped.append({"note_id": nid,
                                "project": project or "",
                                "content": (content or "")[:80]})
                continue
            title = (content or "").strip().splitlines()[0][:120] or "reminder"
            self._conn.execute(
                "INSERT INTO tasks (uuid, project_tag, title, details, "
                "status, source, source_ref, created_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, 'open', "
                "'reminder-migrated', ?, 'tory', ?, ?)",
                (nuuid, canonical, title, (content or "").strip(),
                 "note:{}".format(nid), created_at or _utcnow_iso(),
                 _utcnow_iso()))
            migrated += 1
        self._conn.commit()
        return {"reminder_notes": len(notes), "migrated": migrated,
                "already_migrated": already,
                "skipped_no_project": skipped_no_project,
                "skipped": skipped[:20]}

    # ── OPT-1: canonical project identity ─────────────────────────

    @staticmethod
    def _norm_tag(s):
        """Normalization for alias seeding: case-fold and collapse the
        dash/underscore/space variants that cwd basenames produce."""
        return (s or "").casefold().replace("-", "").replace("_", "").replace(" ", "")

    def resolve_project_tag(self, name):
        """Observed project name -> canonical tag.

        Precedence (OPT-1, no shadowing possible): a canonical tag
        ALWAYS wins; the alias map is consulted only on a canonical
        miss. Unknown names return unchanged (identity), so callers
        can resolve unconditionally.
        """
        if not name:
            return name
        hit = self._conn.execute(
            "SELECT 1 FROM projects WHERE tag = ? LIMIT 1", (name,)
        ).fetchone()
        if hit:
            return name
        row = self._conn.execute(
            "SELECT project_tag FROM project_aliases WHERE alias = ?",
            (name,),
        ).fetchone()
        return row[0] if row else name

    def seed_project_aliases(self, observed):
        """Deterministic OPT-1 seeding: map observed project names onto
        canonical projects.tag rows. Exact matches need no alias;
        case/dash/underscore variants that match exactly ONE canonical
        tag get an auto alias; ambiguous or unmatched names are
        returned for the propose-then-accept campaign (never
        auto-created). Aliases equal to ANY canonical tag are rejected
        (precedence guard, seed path). Idempotent."""
        tags = [r[0] for r in self._conn.execute(
            "SELECT tag FROM projects").fetchall()]
        tag_set = set(tags)
        norm_map = {}
        for t in tags:
            norm_map.setdefault(self._norm_tag(t), []).append(t)
        report = {"observed": 0, "exact": 0, "already_aliased": 0,
                  "seeded": 0, "unmatched": [], "ambiguous": []}
        for name in sorted({n for n in observed if n}):
            report["observed"] += 1
            if name in tag_set:
                report["exact"] += 1
                continue
            existing = self._conn.execute(
                "SELECT 1 FROM project_aliases WHERE alias = ?",
                (name,)).fetchone()
            if existing:
                report["already_aliased"] += 1
                continue
            candidates = norm_map.get(self._norm_tag(name), [])
            if len(candidates) == 1:
                self._conn.execute(
                    "INSERT INTO project_aliases (alias, project_tag, "
                    "source) VALUES (?, ?, 'auto-normalized')",
                    (name, candidates[0]))
                report["seeded"] += 1
            elif len(candidates) > 1:
                report["ambiguous"].append(
                    {"name": name, "candidates": candidates})
            else:
                report["unmatched"].append(name)
        self._conn.commit()
        resolved = (report["exact"] + report["already_aliased"]
                    + report["seeded"])
        report["coverage_pct"] = round(
            100.0 * resolved / report["observed"], 1) if report["observed"] else 100.0
        return report

    def upsert_row(self, table, data):
        """Partial-aware upsert for a whitelisted table.

        `data` is a dict of column→value pairs.

        Semantics (2026-06-06, looper iter #2 followup fix):
        - No PK on auto-PK table → INSERT a new row.
        - PK present + row exists → UPDATE ONLY the supplied columns,
          preserve every other column (content, timestamps, audit
          fields).
        - PK present + no row → INSERT with the supplied columns.

        Previous implementation used `INSERT OR REPLACE` for every PK-
        present case. SQLite's REPLACE semantics DELETE the matching
        row and INSERT a new one - columns not in the partial dict
        default to NULL. That silently nuked note content when callers
        like `note_update(note_id=N, tags="...")` passed partial dicts
        for triage. The looper flagged it; this is the fix.
        """
        if table not in self.WRITABLE_TABLES:
            raise ValueError(f"Table '{table}' is not writable")
        info = self.WRITABLE_TABLES[table]
        pk = info["pk"]

        # OPT-1 precedence guard (write path): one string, one resolution.
        # A new project tag equal to an existing ALIAS would shadow the
        # alias's canonical target, so reject it. (The seed path enforces
        # the mirror rule: no alias equal to an existing canonical tag.)
        if table == "projects" and "tag" in data:
            shadow = self._conn.execute(
                "SELECT project_tag FROM project_aliases WHERE alias = ?",
                (data["tag"],),
            ).fetchone()
            if shadow:
                raise ValueError(
                    "tag '{}' is an alias of project '{}'; use the "
                    "canonical tag or remove the alias first".format(
                        data["tag"], shadow[0]))

        # OPT-2 hierarchy guard: a non-empty org_tag must name a real
        # organization ('' stays allowed = untriaged). Kill switch:
        # CORTEX_ORG_VALIDATE=0 restores the old accept-anything path.
        if (table == "projects" and data.get("org_tag")
                and os.environ.get("CORTEX_ORG_VALIDATE", "1") != "0"):
            valid = self.valid_org_tags()
            if data["org_tag"] not in valid:
                raise ValueError(
                    "unknown org_tag '{}'; valid organizations: {}".format(
                        data["org_tag"], ", ".join(valid)))

        # OPT-3 task contract (tasks are a shared MEMORY layer, federate
        # posture: the business tracker stays the execution tool).
        if table == "tasks":
            data = self._prep_task_write(dict(data))

        # No PK supplied on an auto-PK table → straight INSERT.
        if info["auto_pk"] and pk not in data:
            cols = [k for k in data.keys() if k.replace("_", "").isalnum()]
            vals = [data[k] for k in cols]
            placeholders = ",".join("?" * len(cols))
            col_str = ",".join(cols)
            cur = self._conn.execute(
                f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})",
                vals,
            )
            self._conn.commit()
            return cur.lastrowid

        # PK supplied - check if the row exists. UPDATE if yes (partial-
        # safe), INSERT if no.
        pk_value = data[pk]
        existing = self._conn.execute(
            f"SELECT 1 FROM {table} WHERE {pk} = ? LIMIT 1",
            (pk_value,),
        ).fetchone()

        if existing:
            update_cols = [
                k for k in data.keys()
                if k != pk and k.replace("_", "").isalnum()
            ]
            if not update_cols:
                # Caller passed only the PK - no-op, return the PK.
                return pk_value
            set_clause = ", ".join(f"{c} = ?" for c in update_cols)
            update_vals = [data[k] for k in update_cols]
            update_vals.append(pk_value)
            self._conn.execute(
                f"UPDATE {table} SET {set_clause} WHERE {pk} = ?",
                update_vals,
            )
            self._conn.commit()
            return pk_value

        # Row doesn't exist - INSERT with whatever the caller supplied.
        cols = [k for k in data.keys() if k.replace("_", "").isalnum()]
        vals = [data[k] for k in cols]
        placeholders = ",".join("?" * len(cols))
        col_str = ",".join(cols)
        cur = self._conn.execute(
            f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})",
            vals,
        )
        self._conn.commit()
        return data.get(pk, cur.lastrowid)

    def delete_row(self, table, row_id):
        """Delete a single row by primary key from a whitelisted table."""
        if table not in self.WRITABLE_TABLES:
            raise ValueError(f"Table '{table}' is not writable")
        pk = self.WRITABLE_TABLES[table]["pk"]
        cur = self._conn.execute(
            f"DELETE FROM {table} WHERE {pk} = ?", (row_id,)
        )
        self._conn.commit()
        return cur.rowcount

    def get_table_counts(self):
        """Return row counts for all browsable tables."""
        tables = ["notes", "activities", "searches", "sessions", "projects",
                  "organizations", "tasks", "project_aliases",
                  "time_entries", "computers", "people",
                  "files", "training_examples"]
        counts = {}
        for t in tables:
            try:
                row = self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
                counts[t] = row[0]
            except Exception:
                counts[t] = 0
        return counts

    # --- Context ---

    def get_context(self):
        """Composite query for session startup - returns everything an AI
        needs to understand current state."""
        return {
            "active_projects": self.get_active_projects(),
            "organizations": self.get_organizations(),
            "time_summary": self.get_time_summary(limit=10),
            "recent_sessions": self.get_recent_sessions(5),
            "recent_notes": self.get_recent_notes(10),
            # OPT-3: real (non-proposed) open tasks, the shared memory
            # layer any agent can read/write. pending_reminders stays
            # until the reminder migration passes its live parity check.
            "open_tasks": self.open_tasks(limit=10),
            "pending_reminders": self.get_recent_notes(
                limit=20, note_type="reminder",
            ),
            "recent_decisions": self.get_recent_notes(
                limit=10, note_type="decision",
            ),
            "open_bugs": self.get_recent_notes(limit=20, note_type="bug"),
            "recent_files": self.list_files(limit=10),
            "stats": self.get_stats(),
        }

    # --- Training Examples ---

    def bulk_insert_training_examples(self, examples):
        """Insert multiple training examples in a single transaction."""
        count = 0
        for ex in examples:
            messages = ex.get("messages")
            if not messages:
                continue
            messages_json = json.dumps(messages) if isinstance(messages, (list, dict)) else messages
            self._conn.execute(
                """INSERT INTO training_examples
                   (messages, source, source_id, cycle_id, model, server)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    messages_json,
                    ex.get("source", "learn-cycle"),
                    str(ex.get("source_id", "")),
                    ex.get("cycle_id", 0),
                    ex.get("model", ""),
                    ex.get("server", ""),
                ),
            )
            count += 1
        self._conn.commit()
        return count

    def get_training_examples(self, cycle_id=None, source=None, limit=100, offset=0):
        """Get training examples with optional filters."""
        sql = "SELECT * FROM training_examples"
        params = []
        clauses = []
        if cycle_id is not None:
            clauses.append("cycle_id = ?")
            params.append(cycle_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            # Parse messages JSON back to list
            try:
                d["messages"] = json.loads(d["messages"])
            except (json.JSONDecodeError, TypeError):
                pass
            result.append(d)
        return result

    def count_training_examples(self, cycle_id=None, source=None):
        """Count training examples with optional filters."""
        sql = "SELECT COUNT(*) FROM training_examples"
        params = []
        clauses = []
        if cycle_id is not None:
            clauses.append("cycle_id = ?")
            params.append(cycle_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def get_training_stats(self):
        """Get summary statistics for training examples."""
        total = self.count_training_examples()
        cycles = self._conn.execute(
            "SELECT DISTINCT cycle_id FROM training_examples WHERE cycle_id > 0"
        ).fetchall()
        by_cycle = []
        for row in cycles:
            cid = row[0]
            count = self.count_training_examples(cycle_id=cid)
            first = self._conn.execute(
                "SELECT MIN(created_at), model, server FROM training_examples WHERE cycle_id = ?",
                (cid,)
            ).fetchone()
            by_cycle.append({
                "cycle_id": cid,
                "examples": count,
                "created_at": first[0] if first else None,
                "model": first[1] if first else None,
                "server": first[2] if first else None,
            })
        return {"total": total, "cycles": by_cycle}

    # --- Training Ledger ---

    def get_training_ledger(self):
        """Get the full training ledger as a dict."""
        rows = self._conn.execute("SELECT key, value FROM training_ledger").fetchall()
        ledger = {}
        for row in rows:
            try:
                ledger[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                ledger[row[0]] = row[1]
        return ledger

    def set_training_ledger(self, key, value):
        """Set a training ledger key-value pair."""
        value_json = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        self._conn.execute(
            """INSERT INTO training_ledger (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')""",
            (key, value_json, value_json),
        )
        self._conn.commit()
