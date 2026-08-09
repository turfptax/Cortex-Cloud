"""MCP discovery surface - what a connecting LLM sees in tools/list + the
server instructions. Guards the 2025-11-25-spec discoverability metadata
(titles + behavioral annotations) so connectors can present and safely
auto-approve the tools."""


def _tools():
    from cortex_gateway import mcp_server
    return {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}


def test_expected_tools_present():
    assert set(_tools()) >= {
        "search", "fetch", "cortex_search", "cortex_read",
        "cortex_recent", "cortex_ingest"}


def test_pillar_tools_present():
    assert set(_tools()) >= {
        "cortex_projects_list", "cortex_project_get", "cortex_rules_list",
        "cortex_skills_list", "cortex_skill_get",
        "cortex_project_upsert", "cortex_rule_add", "cortex_skill_log"}


def test_no_people_tool_exposed():
    # People is owner-only (Tory, 2026-07-21): nothing person-shaped on /mcp.
    for name in _tools():
        assert "people" not in name and "person" not in name, name


def test_pillar_reads_annotated_read_only():
    tools = _tools()
    for name in ("cortex_projects_list", "cortex_project_get",
                 "cortex_rules_list", "cortex_skills_list", "cortex_skill_get"):
        ann = tools[name].annotations
        assert ann is not None and ann.readOnlyHint is True, name
        assert ann.openWorldHint is False, name


def test_pillar_writes_annotated_nondestructive_write():
    tools = _tools()
    for name in ("cortex_project_upsert", "cortex_rule_add", "cortex_skill_log"):
        ann = tools[name].annotations
        assert ann.readOnlyHint is False, name
        assert ann.destructiveHint is False, name   # additive/partial, never deletes
        assert ann.openWorldHint is False, name


def test_every_tool_has_title_and_description():
    for name, t in _tools().items():
        assert t.title, f"{name} missing title"
        assert t.description and len(t.description) > 20, f"{name} weak description"


def test_read_tools_annotated_read_only_closed_world():
    tools = _tools()
    for name in ("search", "fetch", "cortex_search", "cortex_read", "cortex_recent"):
        ann = tools[name].annotations
        assert ann is not None and ann.readOnlyHint is True, name
        assert ann.openWorldHint is False, name   # closed corpus


def test_ingest_annotated_as_nondestructive_write():
    ann = _tools()["cortex_ingest"].annotations
    assert ann.readOnlyHint is False
    assert ann.destructiveHint is False           # additive, never deletes
    assert ann.openWorldHint is False


def test_server_instructions_guide_tool_selection():
    from cortex_gateway import mcp_server
    instr = mcp_server.mcp.instructions
    assert instr and "search(query)" in instr and "cortex_ingest" in instr


# ── 2026-08-08: the surface must describe itself ──────────────────────
# Tory's standing invariant: the MCP surface is the PRIMARY product
# surface (AIs are the main users), so its self-description must never
# drift from the tools. These tests are the enforcement.


def test_intro_tool_first_class():
    t = _tools()["cortex_intro"]
    assert t.annotations.readOnlyHint is True
    assert t.annotations.openWorldHint is False
    assert "FIRST" in t.description


def test_instructions_name_every_tool():
    """The guide can only teach tools it mentions. Adding a tool without
    documenting it in `instructions` fails here BY DESIGN: update the
    guide (and _write_contract if it writes) in the same change."""
    from cortex_gateway import mcp_server
    instr = mcp_server.mcp.instructions
    missing = [n for n in _tools() if n not in instr]
    assert not missing, f"tools absent from server instructions: {missing}"


def test_intro_roster_covers_every_tool():
    from cortex_gateway import mcp_server
    assert {r["tool"] for r in mcp_server._tool_roster()} == set(_tools())


def test_write_contract_names_every_write_tool():
    import json
    from cortex_gateway import mcp_server
    contract = json.dumps(mcp_server._write_contract())
    for name, t in _tools().items():
        ann = t.annotations
        if ann is not None and ann.readOnlyHint is True:
            continue
        assert name in contract, f"write tool {name} missing from _write_contract"


def test_new_write_tools_present_and_annotated():
    tools = _tools()
    for name in ("cortex_org_upsert", "cortex_rule_update",
                 "cortex_note_update"):
        assert name in tools, name
        assert tools[name].annotations.readOnlyHint is False, name
    # Correcting a stored memory is the one destructive-flagged write;
    # org/rule updates are soft status flips and partial patches.
    assert tools["cortex_note_update"].annotations.destructiveHint is True
    assert tools["cortex_org_upsert"].annotations.destructiveHint is False
    assert tools["cortex_rule_update"].annotations.destructiveHint is False


def test_build_intro_reports_write_status(monkeypatch):
    from cortex_gateway import mcp_server
    from cortex_gateway.auth import Principal
    monkeypatch.setattr(mcp_server.grants, "can_write", lambda p: False)
    p = Principal(id=1, name="visitor", kind="connector",
                  scopes={"connector:read"}, max_tier="internal",
                  category_filter=[])
    out = mcp_server._build_intro(p)
    assert out["ok"] and out["you"]["can_write"] is False
    assert out["you"]["how_to_get_write"]
    assert out["write_contract"]["people"]       # owner-only stays stated
    assert any(r["tool"] == "cortex_intro" for r in out["read_tools"])
    assert all(r["call"].startswith(r["tool"] + "(")
               for r in out["read_tools"] + out["write_tools"])
