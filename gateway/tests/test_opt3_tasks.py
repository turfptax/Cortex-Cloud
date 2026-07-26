"""OPT-3 tasks: the shared memory layer under projects.

Product DoD under test: an agent records a task and a later agent (or
session) lists it with full context; completing it round-trips; proposed
(overseer-extracted) rows stay out of default lists; every task pull is
parent-stamped to its project for recall grading.
"""
import pytest

from cortex_gateway import corpus_service, pillars_service
from cortex_gateway.auth import Principal


def _connector(scopes=("connector:read", "connector:write")):
    return Principal(id=1, name="openclaw", kind="oauth",
                     scopes=set(scopes), max_tier="internal",
                     category_filter=[], client_id="cli_test")


def _seed(db):
    db.execute("""CREATE TABLE projects (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', status TEXT DEFAULT 'active',
        priority INTEGER DEFAULT 3, category TEXT DEFAULT '',
        org_tag TEXT DEFAULT '', total_hours REAL DEFAULT 0,
        last_touched TEXT DEFAULT '2026-01-01')""")
    db.execute("""CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE NOT NULL,
        project_tag TEXT NOT NULL, title TEXT NOT NULL,
        details TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
        priority INTEGER DEFAULT 3, due_date TEXT DEFAULT '',
        proposed INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'manual', source_ref TEXT DEFAULT '',
        created_by TEXT DEFAULT '', external_ref TEXT DEFAULT '',
        completed_at TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    db.execute("INSERT INTO projects (tag, name) VALUES ('cortex', 'Cortex')")
    db.execute("INSERT INTO tasks (uuid, project_tag, title, proposed, source)"
               " VALUES ('prop-1', 'cortex', 'extracted maybe-task', 1,"
               " 'overseer-extracted')")
    for fn in (db.has_table, db._schema_of, db.table, db.columns):
        fn.cache_clear()


@pytest.fixture()
def opt3(gw, monkeypatch):
    config, db, _ = gw
    _seed(db)
    monkeypatch.setattr(corpus_service.grants, "has_full_access",
                        lambda p: True)
    monkeypatch.setattr(pillars_service.grants, "can_write",
                        lambda p: True)
    return db


def test_task_round_trip_add_list_complete(opt3):
    added = pillars_service.task_add(
        _connector(), title="Wire the org narratives",
        project="cortex", details="OPT-6 prep", priority=2)
    assert added["ok"] is True and added["id"]
    listed = pillars_service.tasks_list(_connector(), project="cortex")
    assert listed["ok"] and listed["total"] == 1
    t = listed["tasks"][0]
    assert t["title"] == "Wire the org narratives"
    assert t["source"] == "agent"
    # provenance: the durable connector handle, not a raw token string
    assert t["created_by"].startswith("connector:")
    done = pillars_service.task_update(_connector(), id=t["id"],
                                       status="done")
    assert done["ok"] is True
    after = pillars_service.tasks_list(_connector(), project="cortex",
                                       status="done")
    assert after["total"] == 1


def test_proposed_rows_hidden_by_default(opt3):
    out = pillars_service.tasks_list(_connector())
    assert all(t["proposed"] == 0 for t in out["tasks"])
    withp = pillars_service.tasks_list(_connector(), include_proposed=True)
    assert any(t["uuid"] == "prop-1" for t in withp["tasks"])


def test_task_pulls_are_parent_stamped(opt3):
    db = opt3
    pillars_service.tasks_list(_connector(), include_proposed=True)
    import sqlalchemy as sa
    with db.engine().connect() as c:
        rows = [dict(r) for r in c.execute(sa.text(
            "SELECT artifact_table, parent_artifact_table, "
            "parent_artifact_id, caller_class FROM pull_events")).mappings()]
    assert rows
    assert all(r["artifact_table"] == "tasks" for r in rows)
    assert all(r["parent_artifact_table"] == "projects" for r in rows)
    assert all(r["parent_artifact_id"] == "cortex" for r in rows)
    assert all(r["caller_class"] == "organic-external" for r in rows)


def test_write_denied_without_grant(opt3, monkeypatch):
    monkeypatch.setattr(pillars_service.grants, "can_write",
                        lambda p: False)
    out = pillars_service.task_add(_connector(), title="x", project="cortex")
    assert out["ok"] is False
