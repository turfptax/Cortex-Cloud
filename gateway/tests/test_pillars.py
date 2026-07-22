"""Pillar MCP tools (Projects / Rules / Skills): gated reads over the
attached corpus, default-deny for unapproved connectors, the connector:write
guard on writes, and write routing to the co-located core.

People is intentionally absent from the surface (owner-only); see
test_mcp_discovery.test_no_people_tool_exposed.
"""
import pytest

from cortex_gateway import corpus_service, pillars_service
from cortex_gateway.auth import Principal


def _connector(scopes=("connector:read",), max_tier="internal", cats=None):
    return Principal(id=1, name="openclaw", kind="connector",
                     scopes=set(scopes), max_tier=max_tier,
                     category_filter=cats or [])


def _app():
    return Principal(id=2, name="hub", kind="app", scopes={"app"},
                     max_tier="restricted", category_filter=[])


def _seed(db):
    """Create the pillar tables in the single-file test DB and populate them.
    In ATTACH mode these live in the cortex/overseer schemas; here they
    collapse to main, which unqualified-name reads resolve identically."""
    db.execute("""CREATE TABLE projects (
        tag TEXT PRIMARY KEY, name TEXT DEFAULT '', status TEXT DEFAULT 'active',
        priority INTEGER DEFAULT 3, description TEXT DEFAULT '',
        category TEXT DEFAULT '', org_tag TEXT DEFAULT '',
        github_url TEXT DEFAULT '', total_hours REAL DEFAULT 0,
        collaborators TEXT DEFAULT '', last_touched TEXT DEFAULT '2026-01-01',
        created_at TEXT DEFAULT '2025-01-01')""")
    db.execute("""CREATE TABLE project_summaries (
        project TEXT PRIMARY KEY, session_count INTEGER DEFAULT 0,
        active_minutes_total INTEGER DEFAULT 0, narrative TEXT DEFAULT '')""")
    db.execute("""CREATE TABLE tech_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, rule TEXT,
        stack TEXT DEFAULT '', situation TEXT DEFAULT '',
        status TEXT DEFAULT 'active', updated_at TEXT DEFAULT '2026-01-01')""")
    db.execute("""CREATE TABLE tech_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, proficiency TEXT DEFAULT '',
        summary TEXT DEFAULT '', tools TEXT DEFAULT '',
        updated_at TEXT DEFAULT '2026-01-01')""")
    db.execute("""CREATE TABLE tech_skill_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id INTEGER, kind TEXT DEFAULT 'note',
        content TEXT, project TEXT DEFAULT '', source TEXT DEFAULT '',
        created_at TEXT DEFAULT '2026-01-02')""")
    db.execute("INSERT INTO projects (tag, name, status, priority) "
               "VALUES ('openmuscle', 'OpenMuscle', 'active', 1)")
    db.execute("INSERT INTO projects (tag, name, status) "
               "VALUES ('archived-thing', 'Old', 'archived')")
    db.execute("INSERT INTO project_summaries (project, session_count, narrative) "
               "VALUES ('openmuscle', 42, 'A long-running hardware thread.')")
    db.execute("INSERT INTO tech_rules (title, rule, stack) VALUES "
               "('Expo perms', 'Use PermissionsAndroid on SDK 51', 'expo,react-native')")
    db.execute("INSERT INTO tech_rules (title, rule, stack, status) VALUES "
               "('Old rule', 'do not', 'x', 'retired')")
    db.execute("INSERT INTO tech_skills (name, proficiency, summary) VALUES "
               "('PCB design', 'expert', 'KiCad boards')")
    db.execute("INSERT INTO tech_skill_log (skill_id, kind, content) VALUES "
               "(1, 'lesson', 'Keep courtyards clear')")
    # The lru caches were primed empty on the freshly reloaded db module.
    for fn in (db.has_table, db._schema_of, db.table, db.columns):
        fn.cache_clear()


class _CoreRec:
    """Stands in for the sync CoreClient. Records posts; returns a valid
    upsert response for /api/cmd and a generic ok for plugin routes."""

    def __init__(self):
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "/api/cmd":
            return {"response": 'RSP:upsert:{"id": "openmuscle"}'}
        if path.endswith("/rules/add"):
            # Mirror the real core route: the row is nested under "rule".
            return {"ok": True, "rule": {"id": 7, "title": payload.get("title")},
                    "created": True}
        if path.endswith("/skills/log"):
            return {"ok": True, "skill": {"id": 3, "name": payload.get("skill")},
                    "entry": {"id": 9, "skill_id": 3,
                              "content": payload.get("content")},
                    "skill_created": False}
        return {"ok": True, "id": 7}


@pytest.fixture()
def granted(monkeypatch):
    """An approved connector connection (grant gate passes); tier/category
    logic still runs. The default-deny path is tested separately."""
    monkeypatch.setattr(corpus_service.grants, "has_full_access", lambda p: True)


# ── Reads ─────────────────────────────────────────────────────────────

def test_projects_list_returns_gated_rows(gw, granted):
    _seed(gw[1])
    out = pillars_service.projects_list(_connector(), status="active")
    assert out["ok"] and out["total"] == 1
    assert out["projects"][0]["tag"] == "openmuscle"
    assert out["projects"][0]["priority"] == 1


def test_projects_list_status_filter(gw, granted):
    _seed(gw[1])
    out = pillars_service.projects_list(_connector())          # no filter
    assert {p["tag"] for p in out["projects"]} == {"openmuscle", "archived-thing"}


def test_project_get_enriches_with_numeric_summary_only(gw, granted):
    _seed(gw[1])
    out = pillars_service.project_get(_connector(), "openmuscle")
    assert out["ok"] and out["project"]["name"] == "OpenMuscle"
    assert out["project"]["summary"]["session_count"] == 42
    # narrative is free text that can name third parties: never on the surface.
    assert "narrative" not in out["project"]["summary"]


def test_project_reads_omit_collaborators_and_narrative(gw, granted):
    """People-owner-only: neither the structured collaborators field nor the
    Overseer narrative may cross the MCP surface, even for an approved
    connector."""
    db = gw[1]
    _seed(db)
    db.execute("UPDATE projects SET collaborators = 'Mara Quinn, Dev Osei' "
               "WHERE tag = 'openmuscle'")
    got = pillars_service.project_get(_connector(), "openmuscle")
    assert "collaborators" not in got["project"]
    assert "Mara Quinn" not in str(got)          # third-party names never leak
    lst = pillars_service.projects_list(_connector())
    assert all("collaborators" not in p for p in lst["projects"])


def test_project_get_missing_is_not_found(gw, granted):
    _seed(gw[1])
    out = pillars_service.project_get(_connector(), "nope")
    assert out["ok"] is False and out["error"] == "not found"


def test_rules_list_default_active_and_stack_filter(gw, granted):
    _seed(gw[1])
    out = pillars_service.rules_list(_connector())
    assert out["total"] == 1 and out["rules"][0]["title"] == "Expo perms"
    hit = pillars_service.rules_list(_connector(), stack="expo")
    assert hit["total"] == 1
    miss = pillars_service.rules_list(_connector(), stack="rust")
    assert miss["total"] == 0


def test_skills_list_and_get_with_log(gw, granted):
    _seed(gw[1])
    lst = pillars_service.skills_list(_connector())
    assert lst["total"] == 1 and lst["skills"][0]["name"] == "PCB design"
    got = pillars_service.skill_get(_connector(), "pcb design")   # case-insensitive
    assert got["ok"] and got["skill"]["proficiency"] == "expert"
    assert got["skill"]["log"][0]["content"] == "Keep courtyards clear"


# ── Default deny (unapproved connector) ───────────────────────────────

def test_unapproved_connector_reads_nothing(gw, monkeypatch):
    _seed(gw[1])
    monkeypatch.setattr(corpus_service.grants, "has_full_access", lambda p: False)
    assert pillars_service.projects_list(_connector())["total"] == 0
    assert pillars_service.rules_list(_connector())["total"] == 0
    assert pillars_service.skills_list(_connector())["total"] == 0
    assert pillars_service.project_get(_connector(), "openmuscle")["ok"] is False


def test_app_token_bypasses_grant(gw):
    # No grant monkeypatch: an app/hub token is not a connector, so the grant
    # gate does not apply and reads succeed.
    _seed(gw[1])
    assert pillars_service.projects_list(_app())["total"] == 2


# ── Read telemetry: pull_events stamped with an mcp: surface ──────────

def test_reads_log_pull_events(gw, granted):
    db = gw[1]
    _seed(db)
    pillars_service.projects_list(_connector(), status="active")
    rows = db.fetchall(
        "SELECT surface FROM pull_events WHERE artifact_table = 'projects'")
    assert rows and rows[0]["surface"] == "mcp:cortex_projects_list"


# ── Write scope guard ─────────────────────────────────────────────────

def test_unapproved_connector_cannot_write(gw, monkeypatch):
    # An unapproved connection (no grant) is denied write, and never touches
    # the core. Read-only scope + no grant -> can_write False.
    rec = _CoreRec()
    monkeypatch.setattr(pillars_service.corpus_writes, "routed", lambda: True)
    monkeypatch.setattr(pillars_service.corpus_writes, "core", lambda: rec)
    ro = _connector()                       # connector:read, no grant
    for out in (pillars_service.project_upsert(ro, tag="x", fields={"name": "X"}),
                pillars_service.rule_add(ro, title="t", rule="r"),
                pillars_service.skill_log(ro, skill="s", content="c")):
        assert out["ok"] is False
        assert out["error"] == "write requires an approved connection"
    assert rec.calls == []                  # never reached the core


def test_approved_connector_can_write_without_write_scope(gw, routed_core, monkeypatch):
    # Approval IS the write gate now (Tory 2026-07-22): a connector with only
    # connector:read but an approved (active+full) grant can log.
    monkeypatch.setattr(pillars_service.grants, "has_full_access", lambda p: True)
    ro = _connector()                       # connector:read only, but approved
    out = pillars_service.rule_add(ro, title="Use WAL", rule="Set WAL on sqlite")
    assert out["ok"] and out["id"] == 7
    assert routed_core.calls and routed_core.calls[0][0] == "/plugins/overseer/rules/add"


# ── Write routing to the core ─────────────────────────────────────────

@pytest.fixture()
def routed_core(monkeypatch):
    rec = _CoreRec()
    monkeypatch.setattr(pillars_service.corpus_writes, "routed", lambda: True)
    monkeypatch.setattr(pillars_service.corpus_writes, "core", lambda: rec)
    return rec


def _writer():
    return _connector(scopes=("connector:read", "connector:write"))


def test_project_upsert_routes_partial_to_cmd_upsert(gw, routed_core):
    out = pillars_service.project_upsert(
        _writer(), tag="openmuscle", fields={"status": "active"})
    assert out["ok"] and out["tag"] == "openmuscle"
    path, payload = routed_core.calls[0]
    assert path == "/api/cmd" and payload["command"] == "upsert"
    assert payload["payload"]["table"] == "projects"
    assert payload["payload"]["data"]["tag"] == "openmuscle"
    assert payload["payload"]["data"]["status"] == "active"


def test_rule_add_routes_to_overseer_route_with_source(gw, routed_core):
    out = pillars_service.rule_add(
        _writer(), title="Use WAL", rule="Set WAL on sqlite", stack="sqlite")
    assert out["ok"] and out["id"] == 7 and out["created"] is True   # from nested "rule"
    path, payload = routed_core.calls[0]
    assert path == "/plugins/overseer/rules/add"
    assert payload["title"] == "Use WAL" and payload["rule"] == "Set WAL on sqlite"
    assert payload["stack"] == "sqlite"
    assert payload["source"] == "connector:openclaw"   # provenance via source field


def test_skill_log_routes_to_overseer_route(gw, routed_core):
    out = pillars_service.skill_log(
        _writer(), skill="React Native", content="raceWithDeadline for fetch",
        kind="lesson")
    assert out["ok"] and out["skill"] == "React Native"
    assert out["entry_id"] == 9 and out["skill_id"] == 3   # from nested "entry"
    path, payload = routed_core.calls[0]
    assert path == "/plugins/overseer/skills/log"
    assert payload["skill"] == "React Native" and payload["kind"] == "lesson"
    assert payload["content"] == "raceWithDeadline for fetch"


def test_write_empty_args_rejected_before_core(gw, routed_core):
    assert pillars_service.rule_add(_writer(), title="", rule="x")["ok"] is False
    assert pillars_service.skill_log(_writer(), skill="s", content="")["ok"] is False
    assert pillars_service.project_upsert(_writer(), tag="", fields={})["ok"] is False
    assert routed_core.calls == []
