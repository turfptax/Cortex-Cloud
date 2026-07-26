"""OPT-0 recall instrumentation: every gateway read writes a CLASSIFIED
pull event, fetched gists stamp their parent project (the abstraction one
layer above), and recent() is no longer the one silent read surface.

These pulls are the raw signal for OPT-7's recall grading; the regression
being prevented is a future read path that silently drops classification
(the pre-OPT-0 state, where caller_class was never set by the gateway and
drill-past was structurally unmeasurable).
"""
import sqlalchemy as sa
import pytest

from cortex_gateway import corpus_service, grants, pillars_service
from cortex_gateway.auth import Principal


def _oauth_connector(client_id="cli_abc"):
    return Principal(id=5, name=f"oauth:{client_id}", kind="oauth",
                     scopes={"connector:read"}, max_tier="internal",
                     category_filter=[], client_id=client_id)


def _hub_app():
    return Principal(id=2, name="phone", kind="app", scopes={"app"},
                     max_tier="restricted", category_filter=[])


def _seed_corpus(db):
    db.execute("""CREATE TABLE summaries_gist (
        id INTEGER PRIMARY KEY AUTOINCREMENT, period_label TEXT DEFAULT '',
        body TEXT NOT NULL, confidence TEXT DEFAULT 'med',
        project_tag TEXT DEFAULT '', created_at TEXT DEFAULT '2026-07-20')""")
    db.execute(
        "INSERT INTO summaries_gist (body, period_label, project_tag) VALUES "
        "('worked on the flexgrid firmware', 'claude-code:abc123def456', "
        "'openmuscle')")
    db.execute(
        "INSERT INTO summaries_gist (body, period_label) VALUES "
        "('untagged older gist', 'session:zz99')")
    for fn in (db.has_table, db._schema_of, db.table, db.columns):
        fn.cache_clear()


def _pulls(db):
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(sa.text(
            "SELECT * FROM pull_events ORDER BY id")).mappings()]


@pytest.fixture()
def opt0(gw, monkeypatch):
    """Seeded corpus + approved connector gate + a registered grant, with
    the per-process connector-handle cache cleared between tests."""
    config, db, _ = gw
    _seed_corpus(db)
    corpus_service._connector_handle.cache_clear()
    grants.upsert_on_connect("cli_abc", "Claude", "claude.ai")
    monkeypatch.setattr(corpus_service.grants, "has_full_access",
                        lambda p: True)
    return db


def test_fetch_classifies_and_stamps_parent(opt0):
    db = opt0
    out = corpus_service.fetch(_oauth_connector(), "g:1")
    assert out["ok"] is True
    rows = _pulls(db)
    assert len(rows) == 1
    r = rows[0]
    # Durable handle: grant name + redirect host, not the churning client_id
    assert r["caller_id"] == "connector:Claude|claude.ai"
    assert r["caller_class"] == "organic-external"
    # Parent stamp: the fetched gist attributes up to its project
    assert r["parent_artifact_table"] == "projects"
    assert r["parent_artifact_id"] == "openmuscle"
    assert r["surface"] == "rest:/v1/item"


def test_fetch_surface_passes_through_and_untagged_has_no_parent(opt0):
    db = opt0
    out = corpus_service.fetch(_oauth_connector(), "g:2",
                               surface="mcp:fetch")
    assert out["ok"] is True
    r = _pulls(db)[0]
    assert r["surface"] == "mcp:fetch"
    assert r["parent_artifact_table"] is None


def test_search_pulls_are_classified(opt0):
    db = opt0
    out = corpus_service.search(_oauth_connector(), "flexgrid",
                                surface="mcp:cortex_search")
    assert out["ok"] is True and out["total"] >= 1
    rows = _pulls(db)
    assert rows and all(r["caller_class"] == "organic-external"
                        for r in rows)
    assert all(r["surface"] == "mcp:cortex_search" for r in rows)


def test_recent_is_logged_and_response_shape_clean(opt0):
    db = opt0
    out = corpus_service.recent(_oauth_connector(), days=3650, limit=10,
                                surface="mcp:cortex_recent")
    assert out["ok"] is True and out["total"] >= 2
    rows = _pulls(db)
    assert len(rows) == out["total"]
    assert all(r["surface"] == "mcp:cortex_recent" for r in rows)
    # the internal _table plumbing never leaks into the response
    assert all("_table" not in item for item in out["items"])


def test_owner_app_reads_are_user_probe_class(opt0):
    db = opt0
    corpus_service.fetch(_hub_app(), "g:1")
    r = _pulls(db)[0]
    assert r["caller_class"] == "user-probe:owner-app"
    assert r["caller_id"] == "owner:phone"


def test_unregistered_connector_still_gets_stable_handle(opt0):
    db = opt0
    corpus_service._connector_handle.cache_clear()
    corpus_service.fetch(_oauth_connector(client_id="cli_ghost"), "g:1")
    r = _pulls(db)[0]
    # no grant row: falls back to the client_id, still explicitly classified
    assert r["caller_id"] == "connector:cli_ghost"
    assert r["caller_class"] == "organic-external"
