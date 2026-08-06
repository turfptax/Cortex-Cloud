"""Schema for the People pillar and the other user-owned tables.

overseer_people, project_people, person_notes and phone_contacts are the
owner's relationship memory. They move as ONE unit because their FOREIGN
KEYs make them inseparable: SQLite rewrites REFERENCES clauses on rename,
cross-database FKs never enforce, and delete_person relies on ON DELETE
CASCADE. The journal, narrative, summary and gist groups sit alongside
them for the same reason, they are the owner's data rather than the AI's.

This module is now ONLY the schema. It used to also carry the OPT-10
Phase C move that relocated these tables out of the overseer's database,
about 390 lines of copy, verify, rename and FK-detachment machinery. That
move has run and there is one corpus database, so the machinery is gone
and every statement here is unqualified: `main` IS the corpus.

Nothing here is a migration. It is create-if-absent, so a fresh deploy
comes up with the same shape the live instance already has.
"""

from __future__ import annotations


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
    """CREATE TABLE IF NOT EXISTS summaries_gist (
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
    "CREATE INDEX IF NOT EXISTS idx_gist_created"
    " ON summaries_gist(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_gist_period"
    " ON summaries_gist(period_label)",
)

_SUMMARY_LOCAL_PAIRS = (
    ("project_summaries", "first_active_at"),
    ("project_summaries", "last_active_at"),
    ("project_summaries", "narrative_updated_at"),
    ("project_summaries", "stats_updated_at"),
)

SUMMARY_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS project_summaries (
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
    "CREATE INDEX IF NOT EXISTS idx_project_summaries_last_active"
    " ON project_summaries(last_active_at)",
)

NARRATIVE_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS temporal_narratives (
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
    "CREATE INDEX IF NOT EXISTS idx_temporal_kind_label"
    " ON temporal_narratives(kind, period_label)",
    "CREATE INDEX IF NOT EXISTS idx_temporal_created"
    " ON temporal_narratives(created_at)",
)

JOURNAL_FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS human_journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'free',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    local_created_at TEXT NOT NULL DEFAULT ''
)""",
    "CREATE INDEX IF NOT EXISTS idx_human_journal_created"
    " ON human_journal_entries(created_at)",
)

# Fresh-install DDL: the FULL live shape (declared columns + the
# script-added merged_into_id / aliases_json / is_provisional + the
# localizer's local_* columns), captured from the live corpus
# 2026-07-27. Existing installs never use these: the mover copies the
# live sqlite_master SQL verbatim instead.
FRESH_DDL = (
    """CREATE TABLE IF NOT EXISTS overseer_people (
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
    """CREATE TABLE IF NOT EXISTS project_people (
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
    """CREATE TABLE IF NOT EXISTS person_notes (
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
    """CREATE TABLE IF NOT EXISTS phone_contacts (
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
    "CREATE INDEX IF NOT EXISTS idx_overseer_people_name_lower"
    " ON overseer_people(LOWER(name))",
    "CREATE INDEX IF NOT EXISTS idx_overseer_people_created"
    " ON overseer_people(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_project_people_project"
    " ON project_people(project)",
    "CREATE INDEX IF NOT EXISTS idx_project_people_person"
    " ON project_people(person_id)",
    "CREATE INDEX IF NOT EXISTS idx_person_notes_person"
    " ON person_notes(person_id)",
    "CREATE INDEX IF NOT EXISTS idx_person_notes_live"
    " ON person_notes(person_id, superseded_by)",
    "CREATE INDEX IF NOT EXISTS phone_contacts_count"
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
            "CREATE TRIGGER IF NOT EXISTS tgr_{t}_local_{c}_ai "
            "AFTER INSERT ON {t} WHEN NEW.{c} IS NOT NULL AND "
            "(NEW.local_{c} IS NULL OR NEW.local_{c} = '') BEGIN "
            "UPDATE {t} SET local_{c} = {e} WHERE rowid = NEW.rowid; END"
            .format(t=table, c=col, e=expr))
        out.append(
            "CREATE TRIGGER IF NOT EXISTS tgr_{t}_local_{c}_au "
            "AFTER UPDATE OF {c} ON {t} WHEN NEW.{c} IS NOT NULL AND "
            "(NEW.{c} != OLD.{c} OR OLD.{c} IS NULL) BEGIN "
            "UPDATE {t} SET local_{c} = {e} WHERE rowid = NEW.rowid; END"
            .format(t=table, c=col, e=expr))
    return out


def ensure_schema(conn, log=None):
    """Create anything missing. Every statement is IF NOT EXISTS, so this
    is a no-op on an instance that already has the tables and the whole
    shape on one that does not.

    Returns the count of statements executed, which is only useful as a
    smoke signal: a fresh install runs all of them, an established one
    runs them all as no-ops.
    """
    groups = (
        (FRESH_DDL, _LOCAL_PAIRS),
        (JOURNAL_FRESH_DDL, ()),
        (NARRATIVE_FRESH_DDL, ()),
        (SUMMARY_FRESH_DDL, _SUMMARY_LOCAL_PAIRS),
        (GIST_FRESH_DDL, _GIST_LOCAL_PAIRS),
    )
    n = 0
    for ddl, pairs in groups:
        for stmt in list(ddl) + (_fresh_triggers(pairs) if pairs else []):
            conn.execute(stmt)
            n += 1
    conn.commit()
    if log:
        log("people_pillar schema ensured (%d statements)" % n)
    return n
