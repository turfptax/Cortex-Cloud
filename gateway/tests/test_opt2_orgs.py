"""OPT-2 organizations: the org layer is readable over the pillar surface
with member counts, the untriaged signal, and org_tag on project lists.

Product DoD under test: an AI listing orgs sees the real grouping
(including the untriaged count trending signal), and an org detail
returns its member projects.
"""
import pytest

from cortex_gateway import corpus_service, pillars_service
from cortex_gateway.auth import Principal


def _connector():
    return Principal(id=1, name="openclaw", kind="connector",
                     scopes={"connector:read"}, max_tier="internal",
                     category_filter=[])


def _seed(db):
    db.execute("""CREATE TABLE organizations (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', org_type TEXT DEFAULT '',
        my_role TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
        notes TEXT DEFAULT '', is_default INTEGER DEFAULT 0,
        external_ref TEXT DEFAULT '', sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT '2026-01-01')""")
    db.execute("""CREATE TABLE projects (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', status TEXT DEFAULT 'active',
        priority INTEGER DEFAULT 3, category TEXT DEFAULT '',
        org_tag TEXT DEFAULT '', total_hours REAL DEFAULT 0,
        last_touched TEXT DEFAULT '2026-01-01')""")
    db.execute("INSERT INTO organizations (tag, name, org_type, sort_order) "
               "VALUES ('open-muscle', 'Open Muscle', 'community', 1)")
    db.execute("INSERT INTO organizations (tag, name, org_type, is_default, "
               "sort_order) VALUES ('unsorted', 'Unsorted', 'thematic', 1, 999)")
    db.execute("INSERT INTO projects (tag, name, org_tag) "
               "VALUES ('openmuscle-flexgrid', 'FlexGrid', 'open-muscle')")
    db.execute("INSERT INTO projects (tag, name, org_tag) "
               "VALUES ('openmuscle-lask5', 'LASK5', 'open-muscle')")
    db.execute("INSERT INTO projects (tag, name) "
               "VALUES ('mystery-thing', 'Untriaged thing')")
    for fn in (db.has_table, db._schema_of, db.table, db.columns):
        fn.cache_clear()


@pytest.fixture()
def opt2(gw, monkeypatch):
    config, db, _ = gw
    _seed(db)
    monkeypatch.setattr(corpus_service.grants, "has_full_access",
                        lambda p: True)
    return db


def test_orgs_list_with_counts_and_untriaged(opt2):
    out = pillars_service.orgs_list(_connector())
    assert out["ok"] is True and out["total"] == 2
    by_tag = {o["tag"]: o for o in out["organizations"]}
    assert by_tag["open-muscle"]["project_count"] == 2
    assert by_tag["unsorted"]["is_default"] == 1
    # the structure-audit health signal: projects nobody has triaged yet
    assert out["untriaged"] == 1


def test_org_get_returns_members(opt2):
    out = pillars_service.org_get(_connector(), "open-muscle")
    assert out["ok"] is True
    org = out["organization"]
    assert org["tag"] == "open-muscle"
    assert {p["tag"] for p in org["projects"]} == {
        "openmuscle-flexgrid", "openmuscle-lask5"}
    # member rows carry org_tag (the OPT-2 list-fields addition)
    assert all(p.get("org_tag") == "open-muscle" for p in org["projects"])


def test_project_list_rows_carry_org_tag(opt2):
    out = pillars_service.projects_list(_connector())
    assert out["ok"] is True
    tags = {p["tag"]: p.get("org_tag") for p in out["projects"]}
    assert tags["openmuscle-flexgrid"] == "open-muscle"
    assert tags["mystery-thing"] == ""


def test_unknown_org_not_found(opt2):
    out = pillars_service.org_get(_connector(), "nope")
    assert out["ok"] is False and out["error"] == "not found"
