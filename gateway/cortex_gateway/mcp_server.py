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

from . import corpus_service, pillars_service
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
mcp = FastMCP(
    "cortex",
    instructions=(
        "Cortex is Tory Moghadam's personal AI memory corpus: an evolving store "
        "of his notes, project context, journal, open questions, patterns, and "
        "AI-synthesized summaries. Use it to ground answers in what Tory has "
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
        "Pillars (structured, first-class):\n"
        "- cortex_projects_list / cortex_project_get: what Tory is working on, "
        "with the Overseer's rollup stats.\n"
        "- cortex_rules_list: Tory's standing tech rules (hard-won engineering "
        "defaults). Read these before advising on his stack.\n"
        "- cortex_skills_list / cortex_skill_get: his tech-skills portfolio.\n"
        "- writes (cortex_project_upsert / cortex_rule_add / cortex_skill_log) "
        "need a write-enabled token, off by default.\n\n"
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
    """Search Tory's Cortex memory corpus. Returns a list of result objects,
    each with an `id` (a Cortex token) you can pass to `fetch` for the full
    content. Covers gists, themes, journal entries, open questions, patterns,
    drift observations, episodes, narratives."""
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
    'q:6'). Returns the body plus linked tokens you can fetch next."""
    p = _principal()
    payload = corpus_service.fetch(p, id)
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
    """Layered search over Tory's corpus. Returns three layers - abstractions
    (themes/patterns/questions), gists (per-session summaries), and raw_refs
    (pointers to source conversations). `kinds` is an optional CSV filter
    (gist,theme,pattern,drift,question,journal,narrative,episode,blindspot).
    `days` restricts to the last N days (0 = all)."""
    return corpus_service.search(_principal(), query, kinds=kinds, days=days,
                                 limit=limit, surface="mcp:cortex_search")


@mcp.tool(title="Read a Cortex token",
          annotations=ToolAnnotations(title="Read a Cortex token",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_read(token: str) -> dict[str, Any]:
    """Resolve a Cortex token to full content plus linked next_tokens for
    graph traversal. Tokens look like g:123 (gist), q:6 (question),
    p:44 (pattern), t:9 (theme), nar:12 (temporal narrative)."""
    return corpus_service.fetch(_principal(), token)


@mcp.tool(title="Recent Cortex activity",
          annotations=ToolAnnotations(title="Recent Cortex activity",
                                      readOnlyHint=True, openWorldHint=False))
def cortex_recent(days: int = 7, limit: int = 40) -> dict[str, Any]:
    """What changed in Tory's corpus over the last N days - recent gists,
    journal entries, narratives, questions, patterns. Good for bootstrapping
    context at the start of a conversation."""
    return corpus_service.recent(_principal(), days=days, limit=limit)


@mcp.tool(title="Ingest into Cortex",
          annotations=ToolAnnotations(title="Ingest into Cortex",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=False))
def cortex_ingest(content: str, kind: str = "note", tags: str = "",
                  project: str = "") -> dict[str, Any]:
    """Push an observation into Cortex's intake pipeline (the overseer loop
    later organizes it). Additive (never deletes/overwrites). Requires a
    connector:write token, which is disabled by default."""
    p = _principal()
    if not p.has("connector:write"):
        return {"ok": False, "error": "token lacks connector:write scope"}
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
    """List Tory's projects (tag, name, status, priority, category, hours,
    last touched). Optional `status` filter (e.g. 'active'). Read-only."""
    return pillars_service.projects_list(_principal(), status=status,
                                         limit=limit)


@mcp.tool(title="Get a Cortex project",
          annotations=ToolAnnotations(title="Get a Cortex project",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_project_get(tag: str) -> dict[str, Any]:
    """Full detail for one project by its tag, plus the Overseer's numeric
    rollup stats (sessions, active minutes, cost) when available. Read-only."""
    return pillars_service.project_get(_principal(), tag)


@mcp.tool(title="List Cortex tech rules",
          annotations=ToolAnnotations(title="List Cortex tech rules",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_rules_list(status: str = "active", stack: str = "",
                      limit: int = 40) -> dict[str, Any]:
    """List Tory's standing tech rules (hard-won engineering defaults: title,
    rule, stack, situation, status). Optional `stack` substring filter and
    `status` (default 'active'). Read these before advising on his stack.
    Read-only."""
    return pillars_service.rules_list(_principal(), status=status,
                                      stack=stack, limit=limit)


@mcp.tool(title="List Cortex skills",
          annotations=ToolAnnotations(title="List Cortex skills",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
def cortex_skills_list(limit: int = 40) -> dict[str, Any]:
    """List Tory's tech-skills portfolio index (name, proficiency, summary,
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
    change (omitted fields are preserved). Requires a connector:write token,
    which is disabled by default. Collaborators are People-pillar data and are
    not editable over MCP."""
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
    """Add (or update by title) a standing tech rule in Tory's living rule log
    that every connecting AI reads. `rule` is the imperative one-liner.
    Requires a connector:write token, off by default."""
    return pillars_service.rule_add(_principal(), title=title, rule=rule,
                                    stack=stack, situation=situation)


@mcp.tool(title="Log a Cortex skill entry",
          annotations=ToolAnnotations(title="Log a Cortex skill entry",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=False))
def cortex_skill_log(skill: str, content: str, kind: str = "note",
                     proficiency: str = "") -> dict[str, Any]:
    """Append an entry (lesson, win, project, tooling, or note) under a skill,
    creating the skill header if new. Requires a connector:write token, off by
    default."""
    return pillars_service.skill_log(_principal(), skill=skill, content=content,
                                     kind=kind, proficiency=proficiency)
