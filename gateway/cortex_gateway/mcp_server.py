"""Remote MCP surface for external AI connectors (ChatGPT / Grok / Claude).

Transport: Streamable HTTP (SSE is deprecated - not built). One endpoint,
mounted into the FastAPI app at /mcp. Stateless so multiple connectors can
hit it independently behind the Cloudflare Tunnel.

Tool layers on the one endpoint:
  • search + fetch - OpenAI-compatible pair over the Memory corpus. ChatGPT
    connectors REJECT any MCP server lacking these with OpenAI's schema;
    Claude + Grok use them too. Kept Memory-only so structured pillar rows
    never leak into a generic reader that expects the corpus token shape.
  • cortex_* Memory - richer layered surface for Claude / Grok / dev-mode
    ChatGPT (cortex_search / cortex_read / cortex_recent / cortex_ingest).
  • cortex_* pillars - Projects / Rules / Skills as first-class tools
    (list + get reads, plus writes gated behind connector:write, off by
    default). People stays owner-only and is not exposed here.

Auth: bearer token validated by middleware (see app.py), principal stashed in
a contextvar that tools read for scope checks + pull-event attribution.
"""
from __future__ import annotations

import contextvars
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import corpus_service, grants, pillars_service
from .auth import Principal
from .config import get_settings


def _transport_security() -> TransportSecuritySettings:
    """Allow the public host through the MCP DNS-rebinding guard. Behind Azure
    App Service / a custom domain the Host header is the public host, which is
    NOT 127.0.0.1 - without this the SDK returns 421 Misdirected Request."""
    hosts = ["127.0.0.1", "127.0.0.1:8430", "localhost", "localhost:8430",
             "127.0.0.1:8000", "localhost:8000"]
    origins: list[str] = []
    pub = get_settings().public_url
    if pub:
        netloc = urlparse(pub).netloc
        if netloc:
            hosts.append(netloc)          # e.g. cortex-gw-8fed.azurewebsites.net
            origins.append(pub)
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)

# Set by the auth middleware (app.py) for each MCP request; read by tools.
current_principal: contextvars.ContextVar[Principal | None] = \
    contextvars.ContextVar("current_principal", default=None)


def _principal() -> Principal:
    p = current_principal.get()
    if p is None:
        # Middleware should have rejected unauthenticated calls already.
        raise PermissionError("no authenticated principal in context")
    return p


# streamable_http_path="/" because we mount the app under "/mcp" in FastAPI;
# the default "/mcp" would double-prefix to /mcp/mcp.
# The owner's display name is interpolated from config (CORTEX_OWNER_NAME) so
# the public repo names no specific person and a friend's deployment shows
# their own name. Default "the owner" keeps an unconfigured install generic.
_OWNER = get_settings().owner_name
mcp = FastMCP(
    "cortex",
    instructions=(
        f"Cortex is {_OWNER}'s personal AI memory corpus: an evolving store "
        "of their notes, project context, journal, open questions, patterns, and "
        f"AI-synthesized summaries. Use it to ground answers in what {_OWNER} has "
        "actually said, done, and is working on rather than guessing.\n\n"
        "Tool guide:\n"
        "- search(query): ranked hits, each with an `id` token (e.g. g:123). "
        "Start here - it is the OpenAI-compatible entry point.\n"
        "- fetch(id): full content for one token, plus linked tokens to fetch next.\n"
        "- cortex_search(query, kinds, days): richer LAYERED results "
        "(abstractions -> gists -> raw refs) with kind/recency filters; prefer it "
        "when you want structure or to scope the search.\n"
        "- cortex_read(token): full content + linked next_tokens for graph walking.\n"
        "- cortex_recent(days): what changed lately; good for bootstrapping context "
        "at the start of a conversation.\n"
        "- cortex_ingest(content): add an observation back into Cortex (write; "
        "needs a write-enabled token, which is off by default).\n\n"
        f"{_OWNER}'s OWN words are first-class and are usually the best "
        "grounding available:\n"
        "- kind `user_note` (alias `note`), tokens un:<id> - their captures "
        "from phone, wearable, web and from AI agents writing on their behalf. "
        "This is also where cortex_ingest writes.\n"
        "- kind `human`, tokens hj:<id> - their journal entries.\n"
        "Do not confuse these with the AI-authored kinds `journal` (the "
        "overseer's own reflection) and `future_note` (the overseer's memos to "
        "its successors, tokens n:<id>).\n\n"
        "Pillars (structured, first-class):\n"
        f"- cortex_projects_list / cortex_project_get: what {_OWNER} is working on, "
        "with Cortex's rollup stats.\n"
        "- cortex_orgs_list / cortex_org_get: the organization layer that "
        "groups projects (companies and thematic groups); org_tag on a "
        "project names its organization.\n"
        "- cortex_tasks_list / cortex_task_add / cortex_task_update: the "
        "shared task memory under each project. Log tasks you learn about "
        "or complete so later agents see the context; this is memory, not "
        "an execution queue.\n"
        f"- cortex_rules_list: {_OWNER}'s standing tech rules (hard-won engineering "
        "defaults). Read these before advising on their stack.\n"
        "- cortex_skills_list / cortex_skill_get: their tech-skills portfolio.\n"
        "- writes (cortex_project_upsert / cortex_rule_add / cortex_skill_log, "
        "plus cortex_ingest) work for any approved connection: logging is a "
        "first-class use, so read and write come together.\n\n"
        "Reads are read-only over a closed corpus. Follow token links "
        "(next_tokens) to traverse related memories. People is intentionally "
        "not exposed over MCP."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_transport_security(),
)


# ── Universal reader pair (OpenAI-compatible) ─────────────────────────


@mcp.tool(title="Search Cortex memory",
          annotations=ToolAnnotations(title="Search Cortex memory",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def search(query: str) -> dict[str, Any]:
    """Search the Cortex memory corpus. Returns a list of result objects,
    each with an `id` (a Cortex token) you can pass to `fetch` for the full
    content. Covers the owner's own notes and journal entries, plus gists,
    themes, open questions, patterns, drift observations, episodes and
    narratives."""
    p = _principal()
    res = corpus_service.search(p, query, surface="mcp:search")
    if not res.get("ok"):
        return {"results": [], "error": res.get("error")}
    results = []
    for h in res.get("hits", []):
        snippet = h.get("snippet") or ""
        title = f"[{h.get('kind')}] {snippet[:80]}"
        results.append({"id": h.get("token"), "title": title, "text": snippet})
    return {"results": results}


@mcp.tool(title="Fetch a Cortex item",
          annotations=ToolAnnotations(title="Fetch a Cortex item",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def fetch(id: str) -> dict[str, Any]:
    """Fetch the full content of one Cortex item by its token id (e.g. 'g:123',
    'q:6', 'un:2219' for one of the owner's notes). Returns the body plus
    linked tokens you can fetch next."""
    p = _principal()
    payload = corpus_service.fetch(p, id, surface="mcp:fetch")
    if not payload.get("ok"):
        return {"id": id, "title": id, "text": "", "metadata": {"error": payload.get("error")}}
    primary = payload.get("primary") or {}
    text = (primary.get("body") or primary.get("narrative")
            or primary.get("text") or primary.get("question") or "")
    title = (primary.get("title") or primary.get("name")
             or payload.get("type") or id)
    return {
        "id": id,
        "title": str(title)[:120],
        "text": text,
        "metadata": {
            "type": payload.get("type"),
            "next_tokens": payload.get("next_tokens", []),
            "created_at": primary.get("created_at"),
        },
    }


# ── Richer Cortex tools (Claude / Grok / dev-mode ChatGPT) ────────────


@mcp.tool(title="Cortex layered search",
          annotations=ToolAnnotations(title="Cortex layered search",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_search(query: str, kinds: str = "", days: int = 0,
                  limit: int = 40) -> dict[str, Any]:
    """Layered search over the Cortex corpus. Returns four layers - notes (the
    owner's own captures), abstractions (themes/patterns/questions), gists
    (per-session summaries), and raw_refs (pointers to source conversations).

    `kinds` is an optional CSV filter. Full list:
      user_note   the owner's own notes (alias: note). Tokens un:<id>.
      human       the owner's journal entries. Tokens hj:<id>.
      gist        per-session summaries          theme      recurring themes
      episode     multi-session arcs             pattern    named patterns
      drift       drift observations             question   open questions
      blindspot   known blindspots               narrative  temporal narratives
      journal     the OVERSEER's own journal (AI-written, not the owner's)
      future_note the OVERSEER's memos to its successors (AI-written)

    Note the two pairs that are easy to confuse: `user_note` and `human` are
    what the OWNER wrote; `future_note` and `journal` are what the AI wrote.

    `days` restricts to the last N days (0 = all)."""
    return corpus_service.search(_principal(), query, kinds=kinds, days=days,
                                 limit=limit, surface="mcp:cortex_search")


@mcp.tool(title="Read a Cortex token",
          annotations=ToolAnnotations(title="Read a Cortex token",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_read(token: str) -> dict[str, Any]:
    """Resolve a Cortex token to full content plus linked next_tokens for
    graph traversal. Tokens look like un:2219 (one of the owner's notes),
    hj:12 (owner journal entry), g:123 (gist), q:6 (question), p:44 (pattern),
    t:9 (theme), nar:12 (temporal narrative), n:44 (an AI memo to future
    overseers, not an owner note)."""
    return corpus_service.fetch(_principal(), token,
                                surface="mcp:cortex_read")


@mcp.tool(title="Recent Cortex activity",
          annotations=ToolAnnotations(title="Recent Cortex activity",
                                      readOnlyHint=True, openWorldHint=False))
def cortex_recent(days: int = 7, limit: int = 40) -> dict[str, Any]:
    """What changed in the corpus over the last N days - the owner's own recent
    notes and journal entries, plus gists, overseer journal entries,
    narratives, questions and patterns. Good for bootstrapping context at the
    start of a conversation."""
    return corpus_service.recent(_principal(), days=days, limit=limit,
                                 surface="mcp:cortex_recent")


@mcp.tool(title="Ingest into Cortex",
          annotations=ToolAnnotations(title="Ingest into Cortex",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=False))
def cortex_ingest(content: str, kind: str = "note", tags: str = "",
                  project: str = "") -> dict[str, Any]:
    """Write an observation into the owner's notes. Additive (never deletes or
    overwrites). Available to any approved connection.

    What you write is immediately findable: cortex_search(kinds="user_note")
    and cortex_read("un:<id>") return it, and it shows up in cortex_recent.
    The response carries the new note id."""
    p = _principal()
    if not grants.can_write(p):
        return {"ok": False, "error": "write requires an approved connection"}
    return corpus_service.ingest(p, content=content, kind=kind,
                                 tags=tags or None, project=project or None)


# ── Pillar tools: Projects / Rules / Skills ───────────────────────────
# People is owner-only (not exposed). Reads reuse the Memory gate; writes
# gate on connector:write (off by default) exactly like cortex_ingest.


@mcp.tool(title="List Cortex projects",
          annotations=ToolAnnotations(title="List Cortex projects",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_projects_list(status: str = "", limit: int = 40) -> dict[str, Any]:
    """List the owner's projects (tag, name, status, priority, category, hours,
    last touched). Optional `status` filter (e.g. 'active'). Read-only."""
    return pillars_service.projects_list(_principal(), status=status,
                                         limit=limit)


@mcp.tool(title="Get a Cortex project",
          annotations=ToolAnnotations(title="Get a Cortex project",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_project_get(tag: str) -> dict[str, Any]:
    """Full detail for one project by its tag, plus Cortex's numeric
    rollup stats (sessions, active minutes, cost) when available. Read-only."""
    return pillars_service.project_get(_principal(), tag)


@mcp.tool(title="List Cortex organizations",
          annotations=ToolAnnotations(title="List Cortex organizations",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_orgs_list() -> dict[str, Any]:
    """List the owner's organizations (companies and thematic groups that
    group projects): tag, name, org_type, member project count, plus the
    count of still-untriaged projects. Read-only."""
    return pillars_service.orgs_list(_principal())


@mcp.tool(title="Get a Cortex organization",
          annotations=ToolAnnotations(title="Get a Cortex organization",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_org_get(tag: str) -> dict[str, Any]:
    """One organization by tag, with its member projects. Read-only."""
    return pillars_service.org_get(_principal(), tag)


@mcp.tool(title="List Cortex tasks",
          annotations=ToolAnnotations(title="List Cortex tasks",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_tasks_list(project: str = "", status: str = "",
                      include_proposed: bool = False,
                      limit: int = 40) -> dict[str, Any]:
    """List tasks in the shared memory layer: things to be done under a
    project, visible to every connecting agent. Optional filters:
    `project` (canonical tag), `status` (open | in_progress | blocked |
    done | cancelled). Curator-extracted proposals are excluded unless
    include_proposed. Read-only."""
    return pillars_service.tasks_list(_principal(), project=project,
                                      status=status,
                                      include_proposed=include_proposed,
                                      limit=limit)


@mcp.tool(title="Add a Cortex task",
          annotations=ToolAnnotations(title="Add a Cortex task",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=False))
def cortex_task_add(title: str, project: str, details: str = "",
                    priority: int = 3, due_date: str = "") -> dict[str, Any]:
    """Record a task under a project so later agents see it: shared
    MEMORY, not an execution queue (the owner's separate tracker handles
    claiming real work). The project must exist; observed names resolve
    through the alias map. Works for any approved connection."""
    return pillars_service.task_add(_principal(), title=title,
                                    project=project, details=details,
                                    priority=priority, due_date=due_date)


@mcp.tool(title="Update a Cortex task",
          annotations=ToolAnnotations(title="Update a Cortex task",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False))
def cortex_task_update(id: int = 0, uuid: str = "", status: str = "",
                       details: str = "", priority: int = 0,
                       due_date: str = "") -> dict[str, Any]:
    """Update a task by id or uuid: status (open | in_progress | blocked |
    done | cancelled), details, priority, or due_date. done/cancelled
    stamp completed_at. Works for any approved connection."""
    return pillars_service.task_update(_principal(), id=id, uuid=uuid,
                                       status=status, details=details,
                                       priority=priority, due_date=due_date)


@mcp.tool(title="List Cortex tech rules",
          annotations=ToolAnnotations(title="List Cortex tech rules",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_rules_list(status: str = "active", stack: str = "",
                      limit: int = 40) -> dict[str, Any]:
    """List the owner's standing tech rules (hard-won engineering defaults: title,
    rule, stack, situation, status). Optional `stack` substring filter and
    `status` (default 'active'). Read these before advising on their stack.
    Read-only."""
    return pillars_service.rules_list(_principal(), status=status,
                                      stack=stack, limit=limit)


@mcp.tool(title="List Cortex skills",
          annotations=ToolAnnotations(title="List Cortex skills",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_skills_list(limit: int = 40) -> dict[str, Any]:
    """List the owner's tech-skills portfolio index (name, proficiency, summary,
    tools). Read-only."""
    return pillars_service.skills_list(_principal(), limit=limit)


@mcp.tool(title="Get a Cortex skill",
          annotations=ToolAnnotations(title="Get a Cortex skill",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_skill_get(name: str) -> dict[str, Any]:
    """One skill's full entry plus its append-only log of lessons, wins,
    projects, and tooling notes. Read-only."""
    return pillars_service.skill_get(_principal(), name)


@mcp.tool(title="Upsert a Cortex project",
          annotations=ToolAnnotations(title="Upsert a Cortex project",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False))
def cortex_project_upsert(tag: str, name: str = "", status: str = "",
                          priority: int = 0, description: str = "",
                          category: str = "", org_tag: str = "",
                          github_url: str = "") -> dict[str, Any]:
    """Create or partially update a project by tag; only the fields you pass
    change (omitted fields are preserved). Works for any approved connection.
    Collaborators are People-pillar data and are not editable over MCP."""
    fields: dict[str, Any] = {}
    if name:
        fields["name"] = name
    if status:
        fields["status"] = status
    if priority:
        fields["priority"] = int(priority)
    if description:
        fields["description"] = description
    if category:
        fields["category"] = category
    if org_tag:
        fields["org_tag"] = org_tag
    if github_url:
        fields["github_url"] = github_url
    return pillars_service.project_upsert(_principal(), tag=tag, fields=fields)


@mcp.tool(title="Add a Cortex tech rule",
          annotations=ToolAnnotations(title="Add a Cortex tech rule",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=False))
def cortex_rule_add(title: str, rule: str, stack: str = "",
                    situation: str = "") -> dict[str, Any]:
    """Add (or update by title) a standing tech rule in the owner's living rule
    log that every connecting AI reads. `rule` is the imperative one-liner.
    Works for any approved connection."""
    return pillars_service.rule_add(_principal(), title=title, rule=rule,
                                    stack=stack, situation=situation)


@mcp.tool(title="Log a Cortex skill entry",
          annotations=ToolAnnotations(title="Log a Cortex skill entry",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=False))
def cortex_skill_log(skill: str, content: str, kind: str = "note",
                     proficiency: str = "") -> dict[str, Any]:
    """Append an entry (lesson, win, project, tooling, or note) under a skill,
    creating the skill header if new. Works for any approved connection."""
    return pillars_service.skill_log(_principal(), skill=skill, content=content,
                                     kind=kind, proficiency=proficiency)
