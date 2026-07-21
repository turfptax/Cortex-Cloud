"""Database access layer - SQLAlchemy Core, dialect-portable.

Three deployment shapes share this module:

  - SQLite single-file (legacy dev)    sqlite:///<file> - one DB holds both
    the relational spine and the interpretive layer.
  - Azure SQL (retiring)               mssql+pyodbc://... via DB_URL.
  - ATTACH topology (solo cloud, P2)   GATEWAY_DB_PATH set - the main DB is
    the gateway's OWN gateway.db (sole writer: tokens/oauth/grants/sync map/
    its own pull_events), with the core's cortex.db + overseer.db ATTACHed
    STRICTLY READ-ONLY (mode=ro URIs; SQLite itself rejects any write into
    them). Unqualified table names resolve main-first then across the
    attached schemas, so read call sites need no changes; corpus writes are
    impossible here by construction and route through the co-located core
    over HTTP (core_client.py).

Gateway-owned tables (gateway_tokens, oauth_clients, oauth_codes, ...) are
defined here with portable types and created via init_schema(). Canonical +
interpretive tables are created elsewhere (CortexDB/OverseerDB) and reflected
lazily here - schema-aware in ATTACH mode.

All SQL is portable: named (:param) binds, timestamps computed in Python, and
case-insensitive matching via LOWER(...) so we never depend on a collation.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import sqlalchemy as sa

from .config import get_settings

# ── Engine ────────────────────────────────────────────────────────────

# Attached corpus schemas, in ATTACH (= name-resolution) order. Main
# (gateway.db) always wins first, which is what routes the gateway's
# pull_events writes to its OWN copy even though overseer.db has a
# table of the same name.
_ATTACH_SCHEMAS = ("cortex", "overseer")


@lru_cache(maxsize=1)
def engine() -> sa.Engine:
    s = get_settings()
    url = s.db_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # check_same_thread off so the pooled connection works across the
        # FastAPI threadpool, matching the previous behaviour.
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = sa.create_engine(url, **kwargs)
    if s.attach_mode:
        _wire_attach(eng, s)
    return eng


def _wire_attach(eng: sa.Engine, s) -> None:
    """Attach the core's two corpus DBs read-only on EVERY pooled
    connection. mode=ro is enforced by SQLite itself: any INSERT/UPDATE/
    DELETE that lands in an attached schema fails with 'attempt to write
    a readonly database'. Missing files fail the connect loudly (start
    the core first; it creates them)."""
    cortex = str(s.cortex_db_path).replace("\\", "/")
    overseer = str(s.overseer_db_path).replace("\\", "/")

    @sa.event.listens_for(eng, "connect")
    def _attach(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            # gateway.db is ours: WAL for concurrent FastAPI threads +
            # Litestream, and a busy timeout so brief writer overlap
            # retries instead of erroring.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute(f"ATTACH DATABASE 'file:{cortex}?mode=ro' AS cortex")
            cur.execute(
                f"ATTACH DATABASE 'file:{overseer}?mode=ro' AS overseer")
        finally:
            cur.close()


def is_sqlite() -> bool:
    return engine().dialect.name == "sqlite"


def is_attach_mode() -> bool:
    return get_settings().attach_mode


# ── Gateway-owned schema (portable DDL) ───────────────────────────────

_metadata = sa.MetaData()

NOW = sa.text("CURRENT_TIMESTAMP")  # valid on sqlite + mssql

gateway_tokens = sa.Table(
    "gateway_tokens", _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("kind", sa.String(20), nullable=False, server_default=sa.text("'connector'")),
    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("key_prefix", sa.String(16)),               # non-secret, for display
    sa.Column("scopes", sa.String(200), nullable=False, server_default=sa.text("'connector:read'")),
    sa.Column("max_tier", sa.String(20), nullable=False, server_default=sa.text("'internal'")),
    sa.Column("category_filter", sa.String(400)),
    sa.Column("note", sa.String(400)),
    sa.Column("created_at", sa.DateTime, server_default=NOW),
    sa.Column("last_used_at", sa.DateTime),
    sa.Column("expires_at", sa.DateTime),                 # NULL = never expires
    sa.Column("revoked_at", sa.DateTime),
    sa.Column("client_id", sa.String(80)),                # OAuth client_id (connection identity)
)

oauth_clients = sa.Table(
    "oauth_clients", _metadata,
    sa.Column("client_id", sa.String(80), primary_key=True),
    sa.Column("client_name", sa.String(200)),
    sa.Column("redirect_uris", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, server_default=NOW),
)

oauth_codes = sa.Table(
    "oauth_codes", _metadata,
    sa.Column("code", sa.String(80), primary_key=True),
    sa.Column("client_id", sa.String(80), nullable=False),
    sa.Column("redirect_uri", sa.String(400), nullable=False),
    sa.Column("code_challenge", sa.String(200), nullable=False),
    sa.Column("scope", sa.String(200), nullable=False, server_default=sa.text("'connector:read'")),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("used", sa.Integer, nullable=False, server_default=sa.text("0")),
)

# Sync v2 (SYNC_CONTRACT_DRAFT.md, RATIFIED 2026-06-10): uuid -> canonical id
# dedup map for idempotent pushes. Lives in the canonical DB so all transports
# (Gateway HTTPS, BLE bridge live-forward) share one dedup space.
sync_row_map = sa.Table(
    "sync_row_map", _metadata,
    sa.Column("uuid", sa.String(64), primary_key=True),
    sa.Column("kind", sa.String(60), nullable=False),
    sa.Column("device", sa.String(80)),
    sa.Column("remote_id", sa.Integer, nullable=False),
    sa.Column("received_at", sa.DateTime, server_default=NOW),
)

# Single-use consent nonces. Issued when the (Easy-Auth-authenticated) consent
# screen renders; redeemed by the human clicking Approve. Lets the approval be a
# GET carrying only this nonce (Azure Easy Auth 403s the authenticated POST in
# the connector popup), and binds the approval to the exact request that was
# shown so it can't be forged or tampered.
oauth_consent = sa.Table(
    "oauth_consent", _metadata,
    sa.Column("nonce", sa.String(80), primary_key=True),
    sa.Column("client_id", sa.String(80), nullable=False),
    sa.Column("redirect_uri", sa.String(400), nullable=False),
    sa.Column("code_challenge", sa.String(200), nullable=False),
    sa.Column("scope", sa.String(200), nullable=False, server_default=sa.text("'connector:read'")),
    sa.Column("state", sa.String(600), server_default=sa.text("''")),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("used", sa.Integer, nullable=False, server_default=sa.text("0")),
)

# Durable record of successful connector authentications, in the canonical
# Cortex store (not just the Azure log stream) so there is a clean, queryable
# history of what connected, when, with which scope/tier, and from where.
connector_connections = sa.Table(
    "connector_connections", _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("client_id", sa.String(80)),
    sa.Column("name", sa.String(200)),
    sa.Column("kind", sa.String(20), server_default=sa.text("'oauth'")),
    sa.Column("scope", sa.String(200)),
    sa.Column("max_tier", sa.String(20)),
    sa.Column("source_ip", sa.String(80)),
    sa.Column("connected_at", sa.DateTime, server_default=NOW),
)

# Per-connection access grant (docs/CONNECTOR_GRANTS_DESIGN.md). One row per
# connector identity (OAuth client_id). Default deny (level=none, status=pending)
# until the owner approves it from the app; the read gate consults this.
connector_grants = sa.Table(
    "connector_grants", _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("client_id", sa.String(80), nullable=False, unique=True),
    sa.Column("name", sa.String(200)),
    sa.Column("redirect_host", sa.String(200)),
    sa.Column("level", sa.String(20), nullable=False, server_default=sa.text("'none'")),
    sa.Column("approval_policy", sa.String(20), nullable=False, server_default=sa.text("'ask'")),
    sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
    sa.Column("first_connected_at", sa.DateTime, server_default=NOW),
    sa.Column("last_connected_at", sa.DateTime),
    sa.Column("granted_at", sa.DateTime),
    sa.Column("granted_by", sa.String(200)),
    sa.Column("updated_at", sa.DateTime, server_default=NOW),
    sa.Column("note", sa.String(400)),
)

# The gateway's OWN pull-event log (connector reads through /v1 + MCP).
# Split-by-writer (P2, docs/CLOUD_MIGRATION.md): the core keeps writing
# ITS pull_events in overseer.db (chat:/mcp: surfaces); the gateway
# writes ONLY this copy. Columns mirror the core's DDL (overseer_db.py)
# so the overseer's F1 union-at-read can project a common shape, plus
# source_ip which only the gateway knows (the exfil monitor uses it).
# In ATTACH mode main-first name resolution routes unqualified
# pull_events statements here, never at overseer.db's copy. In legacy
# single-file mode create_all() finds the core-created table and skips.
pull_events = sa.Table(
    "pull_events", _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("artifact_table", sa.String(80), nullable=False),
    sa.Column("artifact_id", sa.Integer, nullable=False),
    sa.Column("surface", sa.String(80), nullable=False),
    sa.Column("parent_artifact_table", sa.String(80)),
    sa.Column("parent_artifact_id", sa.Integer),
    sa.Column("query_text", sa.Text),
    sa.Column("caller_id", sa.String(200)),
    sa.Column("caller_class", sa.String(40), nullable=False,
              server_default=sa.text("''")),
    sa.Column("source_ip", sa.String(80)),
    sa.Column("pulled_at", sa.DateTime, server_default=NOW),
    sa.Column("created_at", sa.DateTime, server_default=NOW),
    # Mirrors the core's pull_events indexes so the overseer's 7-day
    # F1 windows over the attached copy never table-scan, plus the
    # exfil monitor's per-caller window scans.
    sa.Index("idx_gw_pull_events_artifact", "artifact_table", "artifact_id"),
    sa.Index("idx_gw_pull_events_pulled_at", "pulled_at"),
    sa.Index("idx_gw_pull_events_caller", "caller_id", "pulled_at"),
)

_OWNED = {"gateway_tokens": gateway_tokens,
          "oauth_clients": oauth_clients,
          "oauth_codes": oauth_codes,
          "oauth_consent": oauth_consent,
          "connector_connections": connector_connections,
          "connector_grants": connector_grants,
          "sync_row_map": sync_row_map,
          "pull_events": pull_events}


_migrated = False


def _ensure_column(table: str, col: str, sql_type: str) -> None:
    """Add a column to an EXISTING table if missing. create_all() creates new
    tables but never retrofits columns onto ones that already exist, so a new
    column on an existing table (gateway_tokens.client_id) needs this."""
    insp = sa.inspect(engine())
    if not insp.has_table(table):
        return
    if col in {c["name"] for c in insp.get_columns(table)}:
        return
    kw = "COLUMN " if is_sqlite() else ""   # sqlite: ADD COLUMN; mssql: ADD
    with engine().begin() as c:
        c.execute(sa.text(f"ALTER TABLE {table} ADD {kw}{col} {sql_type}"))
    columns.cache_clear()


def init_schema() -> None:
    """Create the Gateway-owned tables if missing (portable across dialects),
    and run column migrations onto existing tables once per process."""
    _metadata.create_all(engine())
    global _migrated
    if not _migrated:
        _ensure_column("gateway_tokens", "client_id", "VARCHAR(80)")
        _migrated = True


# ── Reflection for canonical + interpretive tables ────────────────────
# In ATTACH mode a corpus table lives in an attached schema, not main,
# so reflection must say WHICH schema. Raw-SQL reads need none of this:
# SQLite resolves unqualified names across main + attached natively.


@lru_cache(maxsize=None)
def _schema_of(name: str) -> str | None:
    """Which schema holds `name`: None = main, 'cortex'/'overseer' = an
    attached corpus DB, or raises LookupError if nowhere. Only meaningful
    in ATTACH mode; single-DB modes always answer None/LookupError."""
    insp = sa.inspect(engine())
    if insp.has_table(name):
        return None
    if is_attach_mode():
        for sch in _ATTACH_SCHEMAS:
            if insp.has_table(name, schema=sch):
                return sch
    raise LookupError(f"table not found in any schema: {name}")


@lru_cache(maxsize=None)
def table(name: str) -> sa.Table:
    """Reflect a table that the Gateway does not own (projects, notes,
    summaries_gist, ...). Cached. Falls back to the owned definition."""
    if name in _OWNED:
        return _OWNED[name]
    schema = _schema_of(name)
    return sa.Table(name, sa.MetaData(), autoload_with=engine(),
                    schema=schema)


@lru_cache(maxsize=None)
def has_table(name: str) -> bool:
    try:
        _schema_of(name)
        return True
    except LookupError:
        return False


@lru_cache(maxsize=None)
def columns(name: str) -> frozenset[str]:
    insp = sa.inspect(engine())
    return frozenset(
        c["name"] for c in insp.get_columns(name, schema=_schema_of(name)))


# ── Query helpers (named params; return plain dicts) ──────────────────


def fetchall(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    with engine().connect() as c:
        return [dict(r) for r in c.execute(sa.text(sql), params or {}).mappings()]


def fetchone(sql: str, params: dict | None = None) -> dict[str, Any] | None:
    with engine().connect() as c:
        row = c.execute(sa.text(sql), params or {}).mappings().first()
        return dict(row) if row else None


def execute(sql: str, params: dict | None = None) -> None:
    with engine().begin() as c:
        c.execute(sa.text(sql), params or {})


def execute_write(sql: str, params: dict | None = None) -> int:
    """Run a write and return the affected row count. Used for atomic
    guarded updates (e.g. single-use code consumption via
    `UPDATE ... WHERE used=0`), where the caller needs to know whether it
    won the race. rowcount is reliable for a single-statement UPDATE/DELETE on
    both SQLite and SQL Server (pymssql) as long as the target table has no
    row-count-suppressing trigger; an AFTER trigger without `SET NOCOUNT ON`
    can make @@ROWCOUNT report the trigger's rows instead. Keep oauth_codes
    trigger-free, or the guard degrades fail-closed (rejects a valid code)."""
    with engine().begin() as c:
        return c.execute(sa.text(sql), params or {}).rowcount


def insert(table_name: str, values: dict[str, Any]) -> Any:
    """Insert a row; return the primary key (autoincrement id, or the provided
    PK for tag/text-id tables). Portable across SQLite + Azure SQL."""
    t = table(table_name)
    with engine().begin() as c:
        result = c.execute(sa.insert(t).values(**values))
        pk = result.inserted_primary_key
        if pk and pk[0] is not None:
            return pk[0]
    # PK was provided by the caller (e.g. projects.tag, people.id).
    for col in ("id", "tag"):
        if col in values:
            return values[col]
    return None
