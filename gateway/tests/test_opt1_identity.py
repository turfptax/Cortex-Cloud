"""OPT-1 project identity: the alias map resolves observed project names
to canonical tags with canonical-always-wins precedence.

Product DoD under test: an owner (or connector) asking for a project by
an old observed name gets IDENTICAL data to asking by the canonical tag,
and a canonical tag can never be shadowed by an alias.
"""
import pytest

from cortex_gateway import corpus_service, pillars_service
from cortex_gateway.auth import Principal


def _connector():
    return Principal(id=1, name="openclaw", kind="connector",
                     scopes={"connector:read"}, max_tier="internal",
                     category_filter=[])


def _seed(db):
    db.execute("""CREATE TABLE projects (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', status TEXT DEFAULT 'active',
        priority INTEGER DEFAULT 3, description TEXT DEFAULT '',
        category TEXT DEFAULT '', org_tag TEXT DEFAULT '',
        github_url TEXT DEFAULT '', total_hours REAL DEFAULT 0,
        last_touched TEXT DEFAULT '2026-01-01',
        created_at TEXT DEFAULT '2025-01-01')""")
    db.execute("""CREATE TABLE project_aliases (
        alias TEXT PRIMARY KEY, project_tag TEXT NOT NULL,
        source TEXT DEFAULT '', created_at TEXT DEFAULT '2026-07-26')""")
    db.execute("INSERT INTO projects (tag, name, priority) "
               "VALUES ('openmuscle-flexgrid', 'FlexGrid', 1)")
    db.execute("INSERT INTO projects (tag, name) "
               "VALUES ('cortex', 'Cortex itself')")
    # observed cwd basename -> canonical
    db.execute("INSERT INTO project_aliases (alias, project_tag, source) "
               "VALUES ('OpenMuscle-FlexGrid', 'openmuscle-flexgrid', "
               "'auto-normalized')")
    # a hostile/buggy alias whose string EQUALS a canonical tag; precedence
    # says the canonical must always win over this mapping
    db.execute("INSERT INTO project_aliases (alias, project_tag, source) "
               "VALUES ('cortex', 'openmuscle-flexgrid', 'tory')")
    for fn in (db.has_table, db._schema_of, db.table, db.columns):
        fn.cache_clear()


@pytest.fixture()
def opt1(gw, monkeypatch):
    config, db, _ = gw
    _seed(db)
    monkeypatch.setattr(corpus_service.grants, "has_full_access",
                        lambda p: True)
    return db


def test_alias_returns_identical_data_to_canonical(opt1):
    by_canonical = pillars_service.project_get(_connector(),
                                               "openmuscle-flexgrid")
    by_alias = pillars_service.project_get(_connector(),
                                           "OpenMuscle-FlexGrid")
    assert by_canonical["ok"] and by_alias["ok"]
    assert by_alias["project"] == by_canonical["project"]
    assert by_alias["project"]["tag"] == "openmuscle-flexgrid"


def test_canonical_tag_always_wins_over_alias(opt1):
    # 'cortex' exists as BOTH a canonical tag and an alias row pointing
    # elsewhere; canonical-wins means the real project is returned and
    # the alias mapping is never consulted.
    out = pillars_service.project_get(_connector(), "cortex")
    assert out["ok"] is True
    assert out["project"]["tag"] == "cortex"
    assert out["project"]["name"] == "Cortex itself"


def test_unknown_name_stays_not_found(opt1):
    out = pillars_service.project_get(_connector(), "never-heard-of-it")
    assert out["ok"] is False and out["error"] == "not found"
