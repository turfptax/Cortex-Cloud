"""Remote MCP surface for external AI connectors (ChatGPT / Grok / Claude).

Transport: Streamable HTTP (SSE is deprecated - not built). One endpoint,
mounted into the FastAPI app at /mcp. Stateless so multiple connectors can
hit it independently behind the Cloudflare Tunnel.

Dual tool layer on the one endpoint:
  • search + fetch - OpenAI-compatible pair. ChatGPT connectors REJECT any
    MCP server lacking these with OpenAI's schema; Claude + Grok use them too.
  • cortex_* - richer layered surface for Claude / Grok / dev-mode
    ChatGPT (cortex_search / cortex_read / cortex_recent / cortex_ingest).

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

from . import corpus_service
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
        "All reads are read-only over a closed corpus. Follow token links "
        "(next_tokens) to traverse related memories."
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
