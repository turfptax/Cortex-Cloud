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

MAINTENANCE INVARIANT (Tory, 2026-08-08): this surface is the PRIMARY
product surface - AIs are the main users of Cortex, the web Hub is the
owner's side window. Whenever a tool is added or changed here:
  1. its docstring IS the tool description every connector reads; keep it
     exact about parameters, defaults, and effects,
  2. name it in `instructions` below (test_mcp_discovery fails otherwise),
  3. if it writes, add it to _write_contract() (also test-enforced).
The roster served by cortex_intro is generated from the live registry at
call time, so it never needs manual upkeep.
"""
from __future__ import annotations

import asyncio
import contextvars
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import Context, FastMCP
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
        "START HERE: call cortex_intro() before anything else. It returns "
        "the live tool roster with exact parameters, whether YOUR "
        "connection may write, the per-pillar write contract (how to add, "
        "modify, and remove each kind of memory), the token legend, and "
        "the gotchas that trip up new connections. cortex_intro(brief=true) "
        "adds the owner-context brief (who they are, active projects, open "
        "questions). When unsure about a parameter, call cortex_intro "
        "instead of guessing.\n\n"
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
        "needs a write-enabled token, which is off by default).\n"
        "- cortex_chat(message): ask the overseer itself, with the whole "
        "corpus in view. Use for synthesis and judgement, not lookups. A "
        "reply takes 45-70s and the tool sends progress notifications while "
        "it waits. If your client cannot wait that long, call "
        "cortex_chat_start and poll cortex_chat_result every 10-15s: that "
        "path has no time limit and the reply is stored server-side, so it "
        "survives a disconnect.\n\n"
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
        "with Cortex's rollup stats. cortex_project_upsert creates or "
        "partially updates one (status='archived' retires it).\n"
        "- cortex_orgs_list / cortex_org_get: the organization layer that "
        "groups projects (companies and thematic groups); org_tag on a "
        "project names its organization. cortex_org_upsert creates or "
        "edits one (is_active=0 retires it).\n"
        "- cortex_tasks_list / cortex_task_add / cortex_task_update: the "
        "shared task memory under each project. Log tasks you learn about "
        "or complete so later agents see the context; this is memory, not "
        "an execution queue. Remove = cortex_task_update(status="
        "'cancelled').\n"
        f"- cortex_rules_list: {_OWNER}'s standing tech rules (hard-won engineering "
        "defaults). Read these before advising on their stack. "
        "cortex_rule_add records a new one; cortex_rule_update refines or "
        "retires one by title (status='retired').\n"
        "- cortex_skills_list / cortex_skill_get: their tech-skills "
        "portfolio; cortex_skill_log appends lessons and wins.\n"
        "- cortex_note_update fixes a note the AI side wrote (source "
        "'ai-generated'); the owner's own captures are read-only here.\n"
        "- Writes work for any approved connection: logging is a "
        "first-class use, so read and write come together. Every remove "
        "is soft (status flips, never deletion).\n\n"
        "Reads are read-only over a closed corpus. Follow token links "
        "(next_tokens) to traverse related memories. People is intentionally "
        "not exposed over MCP."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_transport_security(),
)


# ── Orientation: the run-this-first surface map ───────────────────────
# Everything here is either generated from the live tool registry at
# call time (the roster) or test-enforced against it (the contract), so
# the map cannot drift from the tools. See the maintenance invariant in
# the module docstring.

_TOKEN_LEGEND = {
    "un": "user_note - one of the owner's own notes (cortex_ingest writes here)",
    "hj": "human - an owner journal entry",
    "g":  "gist - per-session summary",
    "t":  "theme - recurring theme",
    "p":  "pattern - named pattern",
    "q":  "question - open question",
    "nar": "narrative - temporal narrative",
    "j":  "journal - the OVERSEER's own reflection (AI-written)",
    "n":  "future_note - overseer memo to its successors (AI-written)",
}


def _write_contract() -> dict:
    """Per-pillar add / modify / remove map. Every write tool must be
    named here (test-enforced); removes are soft by design."""
    return {
        "notes": {
            "add": "cortex_ingest(content, kind='note', tags='', project='')",
            "modify": "cortex_note_update(id, content?, tags?, project?) - "
                      "AI-written notes only (source 'ai-generated')",
            "remove": "not available yet; write a correcting note with "
                      "cortex_ingest that references the un:<id> token",
        },
        "tasks": {
            "add": "cortex_task_add(title, project, details?, priority?, "
                   "due_date?)",
            "modify": "cortex_task_update(id|uuid, title?, status?, "
                      "details?, priority?, due_date?)",
            "remove": "cortex_task_update(status='cancelled') - soft, the "
                      "record stays",
        },
        "projects": {
            "add": "cortex_project_upsert(tag, name, ...)",
            "modify": "cortex_project_upsert - partial: only passed fields "
                      "change",
            "remove": "cortex_project_upsert(status='archived') - archive, "
                      "never delete",
        },
        "organizations": {
            "add": "cortex_org_upsert(tag, name?, org_type?, my_role?, "
                   "notes?)",
            "modify": "cortex_org_upsert - partial",
            "remove": "cortex_org_upsert(is_active=0) - retires it from "
                      "lists; 1 restores",
        },
        "rules": {
            "add": "cortex_rule_add(title, rule, stack?, situation?)",
            "modify": "cortex_rule_update(title, rule?, stack?, situation?, "
                      "status?) - only supplied fields change",
            "remove": "cortex_rule_update(title, status='retired')",
        },
        "skills": {
            "add": "cortex_skill_log(skill, content, kind?, proficiency?) - "
                   "creates the skill on first mention",
            "modify": "cortex_skill_log with proficiency= updates the "
                      "header; entries are append-only",
            "remove": "entries are append-only by design",
        },
        "chat": "cortex_chat(message), or cortex_chat_start + poll "
                "cortex_chat_result when your client cannot wait 45-70s",
        "people": "owner-only; deliberately not exposed over MCP",
    }


def _tool_roster() -> list[dict]:
    """The live tool list, straight from the registry: name, calling
    shape, whether it writes, and the first line of its description.
    Optional parameters carry a trailing '?'."""
    roster = []
    for t in sorted(mcp._tool_manager.list_tools(), key=lambda t: t.name):
        schema = t.parameters if isinstance(t.parameters, dict) else {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        params = [name if name in required else f"{name}?"
                  for name in props]
        ann = t.annotations
        roster.append({
            "tool": t.name,
            "call": "{}({})".format(t.name, ", ".join(params)),
            "writes": not (ann is not None and ann.readOnlyHint is True),
            "what": (t.description or "").strip().splitlines()[0],
        })
    return roster


def _build_intro(principal: Principal,
                 owner_brief: dict | None = None) -> dict:
    """The cortex_intro payload. Module-level so tests can exercise it
    with a bare Principal, no MCP transport needed."""
    writable = grants.can_write(principal)
    roster = _tool_roster()
    out: dict[str, Any] = {
        "ok": True,
        "what_this_is": (
            f"Cortex is {_OWNER}'s persistent AI memory. You are one of "
            "the AIs it exists for: read it to ground your answers in "
            "what has actually happened, and write back what you learn "
            "so the next AI starts warmer than you did."),
        "you": {
            "connection": principal.name,
            "kind": principal.kind,
            "can_write": writable,
        },
        "start_here": [
            "cortex_recent(days=7) for what changed lately",
            "cortex_search(query) to find anything; cortex_read(token) to "
            "go deep; follow next_tokens",
            f"cortex_rules_list() before advising on {_OWNER}'s stack",
            "log what you learn or do: cortex_ingest for observations, "
            "cortex_task_add / cortex_task_update under the project you "
            "touched",
        ],
        "read_tools": [r for r in roster if not r["writes"]],
        "write_tools": [r for r in roster if r["writes"]],
        "write_contract": _write_contract(),
        "token_legend": _TOKEN_LEGEND,
        "gotchas": [
            "cortex_ingest's body parameter is `content` (a `text` alias "
            "is tolerated, but `content` is the real name).",
            "cortex_chat takes 45-70s; if your client times out around "
            "60s, use cortex_chat_start and poll cortex_chat_result.",
            "days=0 in cortex_search means ALL time, not zero days.",
            "kind `user_note`/`human` = the owner's own words; `journal`/"
            "`future_note` = AI-written. Do not confuse them.",
            "Write tools answer ok=false with the reason when your "
            "connection lacks write approval; they never throw.",
        ],
    }
    if not writable:
        out["you"]["how_to_get_write"] = (
            "the owner approves connections from the web Hub; an approved "
            "connection reads AND writes")
    if owner_brief is not None:
        out["owner_brief"] = owner_brief
    return out


@mcp.tool(title="Start here: how to use Cortex",
          annotations=ToolAnnotations(title="Start here: how to use Cortex",
                                      readOnlyHint=True, idempotentHint=True,
                                      openWorldHint=False))
async def cortex_intro(brief: bool = False) -> dict[str, Any]:
    """Call this FIRST when you connect. Returns the live tool roster with
    exact calling shapes, whether YOUR connection can write, the
    per-pillar write contract (how to add, modify, and remove every kind
    of memory), the token legend, and the gotchas that trip up new
    connections. Nothing here is hand-maintained: the roster is read
    from the registry at call time.

    brief=true additionally fetches the owner-context brief (who the
    owner is, what they are working on and thinking about), so one call
    orients you on both the surface and the person."""
    p = _principal()
    owner_brief = None
    if brief:
        s = get_settings()
        try:
            async with _core_client(20.0) as client:
                r = await client.get(f"{s.core_url}/plugins/overseer/intro")
                owner_brief = r.json() if r.status_code == 200 else {
                    "ok": False,
                    "error": f"brief unavailable (HTTP {r.status_code})"}
        except Exception:
            owner_brief = {"ok": False,
                           "error": "brief unavailable right now; the rest "
                                    "of this intro is complete"}
    return _build_intro(p, owner_brief)


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
def cortex_ingest(content: str = "", kind: str = "note", tags: str = "",
                  project: str = "", text: str = "") -> dict[str, Any]:
    """Write an observation into the owner's notes. Additive (never deletes or
    overwrites). Available to any approved connection.

    The body goes in `content` (required; `text` is tolerated as an alias
    for clients that guess, but `content` is the real parameter). `tags`
    is a comma-separated list; `project` is a project tag.

    What you write is immediately findable: cortex_search(kinds="user_note")
    and cortex_read("un:<id>") return it, and it shows up in cortex_recent.
    The response carries the new note id. To fix a note you wrote, use
    cortex_note_update."""
    p = _principal()
    if not grants.can_write(p):
        return {"ok": False, "error": "write requires an approved connection"}
    body = content if (content and content.strip()) else text
    if not body or not body.strip():
        return {"ok": False, "error": (
            "content is required. Schema: content (required), kind "
            "(default 'note'), tags (CSV, default ''), project (default "
            "''). Call cortex_intro() for the full tool map.")}
    return corpus_service.ingest(p, content=body, kind=kind,
                                 tags=tags or None, project=project or None)


@mcp.tool(title="Correct an AI-written note",
          annotations=ToolAnnotations(title="Correct an AI-written note",
                                      readOnlyHint=False, destructiveHint=True,
                                      idempotentHint=True, openWorldHint=False))
def cortex_note_update(id: int, content: str = "", tags: str = "",
                       project: str = "") -> dict[str, Any]:
    """Fix a note the AI side wrote (source 'ai-generated', which includes
    everything cortex_ingest writes): replace its content, retag it, or
    move it to another project. Only the fields you pass change. `id` is
    the numeric part of the un:<id> token.

    The owner's own captures (phone, wearable, journal) are read-only
    over MCP by design: to amend one of those, write a correcting note
    with cortex_ingest that references its un:<id> token."""
    return corpus_service.note_update(_principal(), id=id, content=content,
                                      tags=tags, project=project)


# ── Talking to the overseer ───────────────────────────────────────────
#
# A full reply takes 45 to 70 seconds (measured 47s and 64s). MCP clients
# abandon the call around 60s with error -32001, and that ceiling belongs to
# the CALLER, so raising any server-side timeout does nothing. Two ways out,
# offered together because neither covers every client:
#
#   cortex_chat        emits notifications/progress every 10s. Clients that
#                      sent a progressToken reset their timer on each one, so
#                      the whole reply arrives in a single call. Clients that
#                      did not send one are unaffected and may still time out.
#   cortex_chat_start  returns a job id immediately; the core keeps thinking
#   cortex_chat_result on a background thread and persists the reply whether
#                      or not anyone is still listening. Works everywhere,
#                      and removes the ceiling entirely.

_CHAT_HEARTBEAT_S = 10.0     # how often to reassure the caller
_CHAT_SYNC_READ_S = 170.0    # under the hub's 180s core ceiling


def _core_client(read: float):
    s = get_settings()
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=read, write=30.0, pool=5.0),
        auth=(s.core_username, s.core_token))


@mcp.tool(title="Talk to Cortex",
          annotations=ToolAnnotations(title="Talk to Cortex",
                                      readOnlyHint=False,
                                      destructiveHint=False,
                                      idempotentHint=False,
                                      openWorldHint=False))
async def cortex_chat(message: str, ctx: Context) -> dict[str, Any]:
    """Ask the Cortex overseer a question and get its considered reply.

    This is the conversational counterpart to the read tools. The overseer
    answers with the whole corpus in view: working memory, recent gists and
    themes, open questions, the owner's own notes. Use it when you want a
    synthesis or a judgement rather than a lookup, and prefer the read tools
    when you just need a fact (this costs real money per turn).

    TIMING MATTERS: a reply typically takes 45 to 70 seconds. This tool sends
    progress notifications while it waits, which most clients honour by
    extending their timeout. If your client times out anyway, or if you want
    to do other work meanwhile, use cortex_chat_start and poll
    cortex_chat_result instead. The answer is never lost either way: it is
    written into the Cortex chat thread as soon as it is ready.
    """
    p = _principal()
    if not grants.can_write(p):
        return {"ok": False,
                "error": "chat requires an approved connection"}
    s = get_settings()

    async def _call():
        async with _core_client(_CHAT_SYNC_READ_S) as client:
            r = await client.post(f"{s.core_url}/plugins/overseer/chat",
                                  json={"message": message})
            return r.json()

    task = asyncio.create_task(_call())
    waited = 0.0
    while True:
        done, _ = await asyncio.wait({task}, timeout=_CHAT_HEARTBEAT_S)
        if done:
            break
        waited += _CHAT_HEARTBEAT_S
        try:
            # No total: the wait is open-ended, and a fake percentage that
            # creeps toward a number it never reaches is worse than none.
            await ctx.report_progress(
                progress=waited, total=None,
                message=f"Cortex is thinking ({int(waited)}s)")
        except Exception:
            pass  # a client that cannot receive progress still gets the reply
    try:
        return task.result()
    except httpx.TimeoutException:
        return {"ok": False, "error": "the overseer did not finish in time",
                "hint": "call cortex_chat_start, then poll "
                        "cortex_chat_result; that path has no time limit"}
    except Exception as e:
        return {"ok": False,
                "error": f"Cannot reach core: {type(e).__name__}"}


@mcp.tool(title="Start a Cortex chat (async)",
          annotations=ToolAnnotations(title="Start a Cortex chat (async)",
                                      readOnlyHint=False,
                                      destructiveHint=False,
                                      idempotentHint=False,
                                      openWorldHint=False))
async def cortex_chat_start(message: str) -> dict[str, Any]:
    """Ask the overseer a question without waiting for the answer.

    Returns a job_id straight away. The overseer keeps thinking server-side
    and stores its reply even if you disconnect, so this path has no time
    limit and suits questions worth several minutes of thought.

    Poll cortex_chat_result(job_id) every 10 to 15 seconds. Expect `running`
    for the first 45 to 70 seconds on a typical question.
    """
    p = _principal()
    if not grants.can_write(p):
        return {"ok": False,
                "error": "chat requires an approved connection"}
    s = get_settings()
    try:
        async with _core_client(15.0) as client:
            r = await client.post(
                f"{s.core_url}/plugins/overseer/chat/start",
                json={"message": message})
            return r.json()
    except Exception as e:
        return {"ok": False,
                "error": f"Cannot reach core: {type(e).__name__}"}


@mcp.tool(title="Collect a Cortex chat reply",
          annotations=ToolAnnotations(title="Collect a Cortex chat reply",
                                      readOnlyHint=True, openWorldHint=False))
async def cortex_chat_result(job_id: str) -> dict[str, Any]:
    """Collect the reply for a cortex_chat_start job.

    status is `running` (poll again in 10 to 15s; `elapsed_s` tells you how
    long it has been thinking), `done` (the reply is in `reply`), or `error`.
    A finished reply also lives permanently in the Cortex chat thread, so a
    lost job_id does not lose the answer.
    """
    p = _principal()
    if not grants.can_write(p):
        return {"ok": False,
                "error": "chat requires an approved connection"}
    s = get_settings()
    try:
        async with _core_client(10.0) as client:
            r = await client.get(f"{s.core_url}/plugins/overseer/chat/job",
                                 params={"id": job_id})
            return r.json()
    except Exception as e:
        return {"ok": False,
                "error": f"Cannot reach core: {type(e).__name__}"}


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


@mcp.tool(title="Upsert a Cortex organization",
          annotations=ToolAnnotations(title="Upsert a Cortex organization",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False))
def cortex_org_upsert(tag: str, name: str = "", org_type: str = "",
                      my_role: str = "", notes: str = "",
                      is_active: int = -1) -> dict[str, Any]:
    """Create or partially update an organization (the layer that groups
    projects) by tag; only the fields you pass change. `org_type` is
    free-form (e.g. 'company', 'thematic'). is_active=0 retires the org
    from lists (soft: the row and its member projects stay; 1 restores,
    -1 leaves it unchanged). Point projects at it via
    cortex_project_upsert(org_tag=...). Works for any approved
    connection."""
    fields: dict[str, Any] = {}
    if name:
        fields["name"] = name
    if org_type:
        fields["org_type"] = org_type
    if my_role:
        fields["my_role"] = my_role
    if notes:
        fields["notes"] = notes
    if is_active in (0, 1):
        fields["is_active"] = int(is_active)
    return pillars_service.org_upsert(_principal(), tag=tag, fields=fields)


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
def cortex_task_update(id: int = 0, uuid: str = "", title: str = "",
                       status: str = "", details: str = "",
                       priority: int = 0, due_date: str = "") -> dict[str, Any]:
    """Update a task by id or uuid: title, status (open | in_progress |
    blocked | done | cancelled), details, priority, or due_date. Only
    passed fields change; done/cancelled stamp completed_at, and
    cancelled is the soft remove (the record stays). Works for any
    approved connection."""
    return pillars_service.task_update(_principal(), id=id, uuid=uuid,
                                       title=title, status=status,
                                       details=details, priority=priority,
                                       due_date=due_date)


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


@mcp.tool(title="Amend or retire a Cortex tech rule",
          annotations=ToolAnnotations(title="Amend or retire a Cortex tech rule",
                                      readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False))
def cortex_rule_update(title: str, rule: str = "", stack: str = "",
                       situation: str = "", status: str = "") -> dict[str, Any]:
    """Update an existing rule by its (case-insensitive) title: refine the
    rule text, stack, or situation, or set status to 'retired' (or back
    to 'active'). Only the fields you pass change, so retiring a rule
    never erases its text. Retired rules drop out of the default
    cortex_rules_list view but stay recorded. Works for any approved
    connection."""
    return pillars_service.rule_update(_principal(), title=title, rule=rule,
                                       stack=stack, situation=situation,
                                       status=status)


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
