"""OPT-9 surface tail: hierarchy on REST + sync snapshot pulls.

Product DoD under test: the phone can mirror THE hierarchy (orgs,
projects, tasks arrive as full snapshots the wipe+reinsert engine can
apply), and generic /v1 clients (friend deploys, the pending-op replay)
can read orgs and read/write tasks through the same canonical model.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cortex_gateway.auth import Principal


def _app_client(monkeypatch):
    """Bearer-free client: swap the app-scope dependency for a stub."""
    from cortex_gateway.rest import relational, sync

    app = FastAPI()
    app.include_router(relational.router)
    app.include_router(sync.router)
    stub = Principal(id=1, name="hub", kind="token", scopes={"app", "hub"},
                     max_tier="private", category_filter=[], client_id=None)
    app.dependency_overrides[relational._app] = lambda: stub
    app.dependency_overrides[sync._app] = lambda: stub
    return TestClient(app)


def _seed(db):
    db.execute("""CREATE TABLE organizations (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', org_type TEXT DEFAULT '',
        my_role TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
        notes TEXT DEFAULT '', is_default INTEGER DEFAULT 0,
        external_ref TEXT DEFAULT '', sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT '2026-01-01')""")
    db.execute("""CREATE TABLE org_summaries (
        org_tag TEXT PRIMARY KEY, member_count INTEGER DEFAULT 0,
        narrative TEXT DEFAULT '', narrative_stale INTEGER DEFAULT 0)""")
    db.execute("""CREATE TABLE projects (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', status TEXT DEFAULT 'active',
        priority INTEGER DEFAULT 3, description TEXT DEFAULT '',
        category TEXT DEFAULT '', org_tag TEXT DEFAULT '',
        github_url TEXT DEFAULT '', total_hours REAL DEFAULT 0,
        collaborators TEXT DEFAULT '',
        last_touched TEXT DEFAULT '2026-01-01',
        created_at TEXT DEFAULT '2026-01-01')""")
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
    db.execute("INSERT INTO organizations (tag, name, sort_order) "
               "VALUES ('cortex', 'Cortex', 0)")
    db.execute("INSERT INTO organizations (tag, name, sort_order) "
               "VALUES ('unsorted', 'Unsorted', 999)")
    db.execute("INSERT INTO org_summaries (org_tag, member_count, narrative)"
               " VALUES ('cortex', 2, 'The memory-system org.')")
    db.execute("INSERT INTO projects (tag, name, org_tag) "
               "VALUES ('cortex-core', 'Cortex Core', 'cortex')")
    db.execute("INSERT INTO projects (tag, name, org_tag, status) "
               "VALUES ('old-proj', 'Old', 'cortex', 'archived')")
    db.execute("INSERT INTO tasks (uuid, project_tag, title, status) "
               "VALUES ('t-open', 'cortex-core', 'Open task', 'open')")
    db.execute("INSERT INTO tasks (uuid, project_tag, title, proposed, source)"
               " VALUES ('t-prop', 'cortex-core', 'Proposed task', 1,"
               " 'overseer-extracted')")
    db.execute("INSERT INTO tasks (uuid, project_tag, title, status, "
               "completed_at) VALUES ('t-done-old', 'cortex-core', "
               "'Done long ago', 'done', '2026-01-05 00:00:00')")
    db.execute("INSERT INTO tasks (uuid, project_tag, title, status, "
               "completed_at) VALUES ('t-done-new', 'cortex-core', "
               "'Done just now', 'done', datetime('now'))")
    for fn in (db.has_table, db._schema_of, db.table, db.columns):
        fn.cache_clear()


@pytest.fixture()
def surface(gw, monkeypatch):
    _config, db, _ = gw
    _seed(db)
    return _app_client(monkeypatch), db


# ── Sync snapshot pulls (the phone mirror) ────────────────────────────

def test_snapshot_pull_organizations_full_set(surface):
    client, _db = surface
    r = client.post("/v1/sync/pull", json={
        "device": "pixel", "kind": "organizations", "limit": 50})
    body = r.json()
    assert body["ok"] is True and body["more"] is False
    tags = {row["tag"]: row for row in body["rows"]}
    assert set(tags) == {"cortex", "unsorted"}
    assert tags["cortex"]["narrative"] == "The memory-system org."
    assert tags["unsorted"]["narrative"] == ""


def test_snapshot_pull_projects_includes_all_statuses(surface):
    client, _db = surface
    r = client.post("/v1/sync/pull", json={
        "device": "pixel", "kind": "projects", "limit": 50})
    tags = {row["tag"] for row in r.json()["rows"]}
    assert tags == {"cortex-core", "old-proj"}


def test_snapshot_pull_tasks_answerable_set(surface):
    client, _db = surface
    r = client.post("/v1/sync/pull", json={
        "device": "pixel", "kind": "tasks", "limit": 50})
    uuids = {row["uuid"] for row in r.json()["rows"]}
    # open + proposed + recently-completed; stale done rows stay off the phone
    assert uuids == {"t-open", "t-prop", "t-done-new"}


def test_unknown_pull_kind_still_errors(surface):
    client, _db = surface
    r = client.post("/v1/sync/pull", json={
        "device": "pixel", "kind": "no-such-kind", "limit": 10})
    assert r.json()["ok"] is False


# ── /v1 organizations (read-only) ─────────────────────────────────────

def test_org_list_and_get(surface):
    client, _db = surface
    r = client.get("/v1/organizations")
    assert [o["tag"] for o in r.json()["organizations"]] == ["cortex", "unsorted"]

    r = client.get("/v1/organizations/cortex")
    body = r.json()
    assert body["name"] == "Cortex"
    assert [p["tag"] for p in body["projects"]] == ["cortex-core", "old-proj"]
    assert body["summary"]["narrative"] == "The memory-system org."

    assert client.get("/v1/organizations/nope").status_code == 404


# ── /v1 tasks ─────────────────────────────────────────────────────────

def test_task_list_filters(surface):
    client, _db = surface
    assert {t["uuid"] for t in client.get("/v1/tasks").json()["tasks"]} == \
        {"t-open", "t-done-old", "t-done-new"}
    assert {t["uuid"] for t in client.get(
        "/v1/tasks?status=open").json()["tasks"]} == {"t-open"}
    assert {t["uuid"] for t in client.get(
        "/v1/tasks?include_proposed=true").json()["tasks"]} == \
        {"t-open", "t-prop", "t-done-old", "t-done-new"}
    assert {t["uuid"] for t in client.get(
        "/v1/tasks?project=cortex-core&status=open").json()["tasks"]} == \
        {"t-open"}


def test_task_get_by_uuid(surface):
    client, _db = surface
    assert client.get("/v1/tasks/t-open").json()["title"] == "Open task"
    assert client.get("/v1/tasks/missing").status_code == 404


def test_task_create_and_patch_round_trip(surface):
    """Legacy (non-routed) path: the same route shape the phone's
    pending-op replay uses. Routed-mode validation lives core-side."""
    client, _db = surface
    r = client.post("/v1/tasks", json={
        "uuid": "t-new-1", "project_tag": "cortex-core",
        "title": "Phone-created task", "priority": 2})
    assert r.status_code == 200
    assert r.json()["uuid"] == "t-new-1" and r.json()["status"] == "open"

    r = client.patch("/v1/tasks/t-new-1", json={"status": "done"})
    assert r.status_code == 200 and r.json()["status"] == "done"

    assert client.patch("/v1/tasks/ghost",
                        json={"status": "done"}).status_code == 404
