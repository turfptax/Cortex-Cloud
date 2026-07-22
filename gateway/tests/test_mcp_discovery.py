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
