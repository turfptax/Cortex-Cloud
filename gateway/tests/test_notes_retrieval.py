"""The owner's own notes are reachable through the retrieval surface.

Until 2026-08-04 the `notes` table was a write-only black hole on the MCP
surface: cortex_ingest could put a row in and no tool could ever get it back,
because SEARCH_TARGETS had no entry for it and kinds="note" resolved to the
overseer's own memos instead. These tests pin the fix, the token continuity it
had to preserve, and the starvation bug found next door to it.
"""
import sqlalchemy as sa

from cortex_gateway import corpus_service as cs
from cortex_gateway.auth import Principal
from cortex_gateway.search_maps import PREFIX_TARGETS, SEARCH_TARGETS, resolve_kind


# ── helpers ──────────────────────────────────────────────────────────

def _connector(max_tier="internal"):
    return Principal(id=1, name="probe", kind="connector",
                     scopes={"connector:read"}, max_tier=max_tier,
                     category_filter=[])


def _app():
    return Principal(id=2, name="hub", kind="app", scopes={"app"},
                     max_tier="restricted", category_filter=[])


def _mk_notes_table(db):
    """Create the notes table the way cortex.db has it (minus local_created_at,
    which timestamp_localizer adds at runtime and which nothing here needs)."""
    with db.engine().begin() as c:
        c.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS notes ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " content TEXT NOT NULL,"
            " tags TEXT DEFAULT '',"
            " project TEXT DEFAULT '',"
            " note_type TEXT DEFAULT 'note',"
            " source TEXT DEFAULT 'ble',"
            " session_id TEXT,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')))"))
    db.columns.cache_clear()
    db.has_table.cache_clear()
    db.table.cache_clear()


def _add_note(db, content, *, note_type="note", source="mobile", project="",
              created_at="2026-08-04 04:24:07"):
    with db.engine().begin() as c:
        r = c.execute(sa.text(
            "INSERT INTO notes (content, note_type, source, project, created_at)"
            " VALUES (:c, :nt, :s, :p, :ts)"),
            {"c": content, "nt": note_type, "s": source, "p": project,
             "ts": created_at})
        return r.lastrowid


def _mk_simple_table(db, name, body_col):
    with db.engine().begin() as c:
        c.execute(sa.text(
            f"CREATE TABLE IF NOT EXISTS {name} ("
            f" id INTEGER PRIMARY KEY AUTOINCREMENT,"
            f" {body_col} TEXT,"
            f" created_at TEXT DEFAULT (datetime('now')))"))
    db.columns.cache_clear()
    db.has_table.cache_clear()
    db.table.cache_clear()


def _add_simple(db, name, body_col, text):
    with db.engine().begin() as c:
        c.execute(sa.text(f"INSERT INTO {name} ({body_col}) VALUES (:b)"),
                  {"b": text})


def _granted(monkeypatch):
    monkeypatch.setattr(cs.grants, "has_full_access", lambda p: True)
    monkeypatch.setattr(cs, "_record_pull", lambda *a, **k: None)


# ── the map itself ───────────────────────────────────────────────────

def test_user_note_kind_points_at_the_owners_notes():
    assert SEARCH_TARGETS["user_note"][0] == "notes"


def test_note_alias_means_the_owners_notes_not_the_ai_memos():
    # The whole bug in one assertion: an agent typing the ordinary English word
    # used to get future_overseer_notes back.
    assert resolve_kind("note") == "user_note"
    assert SEARCH_TARGETS[resolve_kind("note")][0] == "notes"


def test_future_note_keeps_the_n_prefix_for_token_continuity():
    # n:<id> tokens are already in circulation inside working memory and in
    # other agents' notes; renaming the KEY must not move the PREFIX.
    assert SEARCH_TARGETS["future_note"][2] == "n"
    assert PREFIX_TARGETS["n"][0] == "future_overseer_notes"
    assert PREFIX_TARGETS["un"][0] == "notes"


# ── search ───────────────────────────────────────────────────────────

def test_search_returns_a_user_note(gw, monkeypatch):
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    nid = _add_note(db, "A sync bug is confirmed on the server side",
                    note_type="observation", source="ai-generated")

    res = cs.search(_connector(), "sync bug", kinds="user_note")

    assert res["ok"]
    hit = next(h for h in res["hits"] if h["artifact_table"] == "notes")
    assert hit["token"] == f"un:{nid}"
    assert hit["kind"] == "user_note"
    assert "sync bug" in hit["snippet"].lower()
    # extras carry the provenance an agent needs to weigh the note
    assert hit["extras"]["note_type"] == "observation"
    assert hit["extras"]["source"] == "ai-generated"


def test_notes_get_their_own_response_layer(gw, monkeypatch):
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    _add_note(db, "distinctive phrase alpha")

    res = cs.search(_connector(), "distinctive phrase alpha")

    assert len(res["notes"]) == 1
    # raw owner input is not an "abstraction"; nothing was inferred here
    assert res["notes"][0] not in res["abstractions"]


def test_days_filter_applies_to_notes(gw, monkeypatch):
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    _add_note(db, "ancient capture zulu", created_at="2020-01-01 00:00:00")

    assert cs.search(_connector(), "ancient capture zulu",
                     kinds="user_note")["total"] == 1
    assert cs.search(_connector(), "ancient capture zulu",
                     kinds="user_note", days=7)["total"] == 0


# ── the starvation bug found next door ───────────────────────────────

def test_late_kinds_are_not_starved_by_earlier_ones(gw, monkeypatch):
    """Regression: search used to break the OUTER kind loop on limit_total.

    With 11 kinds at 5 rows each and a default limit of 40, any query matching
    the first eight kinds consumed the whole budget and the last kinds were
    never queried at all. Iteration order put the two tables the OWNER wrote
    at the back of that line. This test fails on the old code.
    """
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    # Two AI-authored kinds that sort BEFORE user_note, each able to fill
    # per_kind(5). Together they exactly exhaust limit_total(10), which is what
    # made the old outer-loop break skip every remaining kind.
    _mk_simple_table(db, "summaries_gist", "body")
    _mk_simple_table(db, "overseer_journal", "body")
    for _ in range(8):
        _add_simple(db, "summaries_gist", "body", "collision term omega")
        _add_simple(db, "overseer_journal", "body", "collision term omega")
    _add_note(db, "collision term omega")

    res = cs.search(_connector(), "collision term omega", limit=10)

    kinds = {h["kind"] for h in res["hits"]}
    assert "user_note" in kinds, (
        "the owner's note lost its seat to earlier kinds")
    assert len(res["hits"]) <= 10


# ── fetch / token resolution ─────────────────────────────────────────

def test_fetch_resolves_a_user_note_token(gw, monkeypatch):
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    nid = _add_note(db, "the body of the note")

    res = cs.fetch(_connector(), f"un:{nid}")

    assert res["ok"]
    assert res["primary"]["content"] == "the body of the note"


def test_fetch_still_resolves_old_ai_memo_tokens(gw, monkeypatch):
    # Token continuity: n:<id> must keep meaning what it always meant.
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_simple_table(db, "future_overseer_notes", "body")
    _add_simple(db, "future_overseer_notes", "body", "memo to my successor")

    res = cs.fetch(_connector(), "n:1")

    assert res["ok"]
    assert res["primary"]["body"] == "memo to my successor"


# ── recent ───────────────────────────────────────────────────────────

def test_recent_includes_the_owners_notes(gw, monkeypatch):
    """The corpus used to report 'quiet day, nothing written' on days the owner
    wrote notes, because recent() only read AI-authored tables."""
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    _add_note(db, "wrote three notes today",
              created_at=cs.datetime.now(cs.timezone.utc)
              .strftime("%Y-%m-%d %H:%M:%S"))

    res = cs.recent(_connector(), days=2)

    assert any(i["kind"] == "user_note" for i in res["items"])


# ── the connector policy clamp ───────────────────────────────────────

def test_connector_policy_can_clamp_note_bodies(gw, monkeypatch):
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    _add_note(db, "sensitive capture yankee")

    monkeypatch.setenv("CORTEX_NOTES_CONNECTOR_POLICY", "title_only")
    _config.get_settings.cache_clear()
    try:
        conn_hit = cs.search(_connector(), "sensitive capture yankee",
                             kinds="user_note")["hits"][0]
        app_hit = cs.search(_app(), "sensitive capture yankee",
                            kinds="user_note")["hits"][0]
        assert conn_hit["gated"] is True
        assert "yankee" not in conn_hit["snippet"]
        # the owner's own surfaces are never clamped by this knob
        assert app_hit["gated"] is False
        assert "yankee" in app_hit["snippet"]
    finally:
        monkeypatch.delenv("CORTEX_NOTES_CONNECTOR_POLICY", raising=False)
        _config.get_settings.cache_clear()


def test_default_policy_leaves_notes_readable(gw, monkeypatch):
    _config, db, _oauth = gw
    _granted(monkeypatch)
    _mk_notes_table(db)
    _add_note(db, "ordinary capture xray")
    _config.get_settings.cache_clear()

    hit = cs.search(_connector(), "ordinary capture xray",
                    kinds="user_note")["hits"][0]

    assert hit["gated"] is False
    assert "xray" in hit["snippet"]
