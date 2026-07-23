# Cortex Gateway - Design (Phase 0 contract)

**Status:** DRAFT for Tory's review. Locks the contracts before code.
**Date:** 2026-05-28
**Owner direction:** [[cortex_phone_app_cloud_mcp_direction]]

---

> ## ⚠️ Amendment 2026-05-28 - deployment target is Microsoft Azure
>
> The hosting decision below (Cloudflare Tunnel → Raspberry Pi 5, single
> canonical SQLite) is **superseded**. The Gateway deploys to **Azure in the
> owner's own tenant**:
> - **Hosting:** Azure Container Apps (recommended) or App Service.
> - **Database:** Azure Database for **PostgreSQL Flexible Server** (recommended
> - `pg_trgm` + `pgvector`) or Azure SQL. The SQLite data layer must be ported
>   (it is not a config change).
> - **Overseer loop:** relocates to an Azure worker/Job (no more Pi).
> - **Domain/TLS:** Azure-managed cert; Cloudflare DNS/WAF optional *in front*.
>
> The REST surface (§5) and sensitivity model (§8) still hold. Auth (§4) and the
> MCP surface (§6) have since changed and are updated in place below: the MCP
> surface is now 14 tools (not the six first sketched in §6), and connector
> authorization moved to the per-connection `connector_grants` approval model
> (see `docs/CONNECTOR_GRANTS_DESIGN.md`). See `HANDOFF_cortex_phone_cloud.md` §8
> and `deploy/azure-deploy.md` for the current deployment design. Read §2/§9/§10
> below as historical (the Pi-era plan).

---

## 1. Purpose

The Gateway is the single authenticated, internet-reachable service that fronts
the canonical Cortex corpus. It serves two audiences over one process:

- **The phone app** (React Native + Expo) - REST, for data entry + viewing.
- **External AI connectors** (ChatGPT, Grok SuperHeavy, Claude) - remote MCP
  over Streamable HTTP, for context pull + ingest.

It replaces the Pi's raw `http.server` + hardcoded `cortex:cortex` Basic Auth +
the `/api/cmd` `CMD:` protocol tunnel. The desktop Hub demotes to an ops
dashboard that points at the Gateway.

Mission frame (Tory): **turn raw chat into organized relational datasets** for
future human + AI use. The Gateway is both the *reader* (serve organized data)
and the *intake* (accept raw, hand to the overseer loop that organizes it).

## 2. Topology

```
Phone (Expo)  ──REST + bearer──┐
                               ▼
Claude/ChatGPT/Grok ─MCP/HTTPS─►  Cloudflare Tunnel  ─►  Pi 5 home node
                                  (public HTTPS, no             │
                                   inbound ports)               ▼
                                              ┌─────────────────────────────┐
                                              │  cortex-gateway (FastAPI)    │
                                              │   • REST   • MCP (Streamable)│
                                              │   • auth   • sensitivity gate│
                                              └──────────────┬──────────────┘
                                                             │ same process / localhost
                                              ┌──────────────▼──────────────┐
                                              │ canonical SQLite (Pi 5)      │
                                              │  cortex.db + overseer.db     │
                                              │ overseer loop (migrated      │
                                              │  from .25) + vault generator │
                                              └──────────────────────────────┘
Wearable Pis (.25, .132) → capture clients that sync UP to the Pi 5. Not canonical.
```

**Canonical move:** the Pi 5 takes over `cortex.db` + `overseer.db` from the
Orange Pi `.25`; the overseer loop runs on the Pi 5. See §10 for the migration.

## 3. Data the Gateway owns

Two SQLite files (WAL mode), both on the Pi 5, both behind the Gateway.

**`cortex.db` - the relational spine (Function 2 source of truth):**
`sessions, notes, projects, activities, searches, time_entries, people,
organizations, computers` (+ legacy `pet_interactions`, frozen).

**`overseer.db` - the interpretive layer (Function 1 payload):**
`summaries_gist, summaries_theme, summaries_episode, open_questions, patterns,
drift_observations, future_overseer_notes, temporal_narratives, llm_calls,
raw_pointers, tags`.

The Gateway reads/writes `cortex.db` directly. It reaches the interpretive layer
through the existing overseer search/detail functions (`corpus.search_corpus`,
the detail-token resolver) - not raw SQL - so sensitivity + pull-event logic
stays in one place.

## 4. Auth model

Single-user system (the owner's accounts), but internet-facing → must be real.

**Phase 1 - bearer tokens (ship first):**
- One opaque token per client, stored hashed in a new `gateway_tokens` table
  (`id, name, token_hash, scopes, created_at, last_used_at, revoked_at`).
- `Authorization: Bearer <token>`. Scopes: `app` (full REST) and the connector
  scopes `connector:read` / `connector:write`. NB (2026-07): a connector's reads
  AND writes are both gated by the per-connection `connector_grants` approval,
  not by the scope string. A connection the owner approved to `full`
  (`has_full_access`) reads the corpus and, through `grants.can_write`, writes to
  it too; approval grants both. The `connector:write` scope now matters only for
  static CLI tokens. See `docs/CONNECTOR_GRANTS_DESIGN.md`.
- Each connector (Claude / ChatGPT / Grok) gets its own named token →
  per-service revocation + per-service pull auditing.
- Works today for: Grok custom MCP, ChatGPT (URL + token), Claude API connector.

**Phase 2 - OAuth 2.1 + PKCE (for consumer-UI connectors):**
- Needed for the polished claude.ai / ChatGPT app-UI custom-connector flows.
- Authorization-code + PKCE (S256 only), single-user consent screen gated behind
  an interactive Entra login. Claude callback: `https://claude.ai/api/mcp/auth_callback`.
- Bearer tokens remain valid for the phone app + API connectors.
- **Disabled by default** (`GATEWAY_OAUTH_ENABLED`): it is a public token-minting
  surface and no connector uses it yet. When enabled it is scope-capped (never
  admin/app), issues short-lived tokens, uses atomic single-use codes, and echoes
  RFC 9207 `iss`. Full best-practice contract + deferred items:
  [`docs/OAUTH_2_1.md`](docs/OAUTH_2_1.md). Hardened per the 2026-06-23 security
  audit (`_audit_backups/GATEWAY_SECURITY_AUDIT_2026-06-23.md`).

No secrets in the repo. Tokens minted via a CLI / Hub admin screen.

## 5. REST surface (phone app)

Prefix `/v1`. JSON. Bearer `app` scope. Mirrors the Hub data contracts so the
Expo client reuses the existing TS shapes.

| Method | Path | Purpose |
|---|---|---|
| GET/POST/PATCH | `/v1/projects` , `/v1/projects/{tag}` | Project oversight (list/create/update). `status, priority, category, org_tag, total_hours, collaborators`. |
| GET | `/v1/projects/{tag}/summary` | Overseer project narrative + metrics (read from interpretive layer). |
| GET/POST | `/v1/notes` , `/v1/notes/{id}` | Quick-capture + browse. `content, tags, project, note_type, source`. |
| GET/POST | `/v1/journal` | Human journal entries (text). Voice → §7. |
| GET/POST/PATCH | `/v1/people` , `/v1/people/{id}` | People (+ project links). |
| GET/POST | `/v1/time` | Time entries. |
| GET | `/v1/search?q=&kinds=&project=&limit=` | The unified search (same engine as MCP `search`). Layered results. |
| GET | `/v1/item/{token}` | Fetch one item full (gist/theme/note/project/...) - same as MCP `fetch`. |
| GET | `/v1/recent?days=` | What changed (context bootstrap + app home feed). |
| GET | `/v1/narratives?period=` | Daily/weekly/monthly/yearly temporal narratives (viewing). |
| POST | `/v1/ingest` | Push raw content into the intake pipeline (→ overseer organizes it). |

All timestamps follow the locked rule: store UTC + local-with-offset, render
local. See [[feedback_time_always_local_with_tz]].

## 6. MCP surface (connectors)

**Transport:** Streamable HTTP (no SSE - deprecated). Root path + HEAD +
session management per the 2026 spec. One endpoint, e.g. `/mcp`.

**Dual tool layer - both on the same endpoint:**

*Universal reader pair (ChatGPT hard requirement; Claude + Grok use too):*
| Tool | OpenAI-compatible shape | Maps to |
|---|---|---|
| `search(query)` | → `{ results: [{ id, title, url? }] }` | `corpus.search_corpus` across the 8 kinds (gist/theme/episode/question/pattern/drift/journal/narrative) + cortex.db notes/projects/people. `id` = a Cortex token (`g:123`, `p:employer`, `note:88`). |
| `fetch(id)` | → `{ id, title, text, url?, metadata }` | detail-token resolver → full body + frontmatter + linked tokens. Applies sensitivity gating. |

*Richer Cortex tools (Claude / Grok / dev-mode ChatGPT):*
`cortex_search` (layered returns: abstractions→gists→raw_refs),
`cortex_read(token)`, `cortex_recent(days)`,
`cortex_ingest(content,kind,tags,project)`.

*Pillar tools (Projects / Rules / Skills as first-class):*
reads `cortex_projects_list`, `cortex_project_get`, `cortex_rules_list`,
`cortex_skills_list`, `cortex_skill_get`; writes `cortex_project_upsert`,
`cortex_rule_add`, `cortex_skill_log`. People is owner-only and not exposed
here; the project narrative + `collaborators` are withheld.

Fourteen tools total: the `search`/`fetch` pair above plus these twelve. Writes
work for any approved connection (see §4); reads and writes share the same
`connector_grants` gate.

**Never exposed on the public MCP:** `shell_exec`, `wifi_*`, raw
`query/upsert/delete`, `pet_*`, sibling/admin tools. Those stay LAN/admin-only.

`search`→`fetch` IS the three-layer read pattern (abstraction-first,
drill-on-demand). Every `fetch` / `cortex_read` writes a `pull_events` row →
refinement signal for the overseer. See [[three_layer_architecture_design_seed]].

## 7. Voice capture

Phone records audio → `POST /v1/journal` (or `/v1/ingest`) with the audio blob →
Gateway transcribes (whisper.cpp on the Pi 5, reusing the existing transcribe
service) → stores transcript as the entry. Async ack-then-poll, mirroring the
Hub's current transcription flow.

## 8. Sensitivity gating (Slice 13)

Enforced at the Gateway boundary, every read path (REST + MCP):
- `public` / `internal` → full body.
- `confidential` → sanitized body (the Slice 13 sanitized-gist prompt output),
  title preserved.
- `restricted` → title-only stub; body withheld even from connectors.
- **Raw never leaves via connectors by default**; `fetch`/`cortex_read` of a
  raw pointer applies the tier at fetch time.

Per-connector token scope can further restrict (e.g. a "work" connector token
sees `internal` but not `personal`).

## 9. Tech stack & deployment

- **FastAPI** + **uvicorn**, Python 3.11+. (Modern, async, replaces raw
  `http.server`; the Hub backend is already FastAPI so patterns carry over.)
- **MCP**: the official Python SDK `FastMCP` in Streamable-HTTP mode, mounted
  into the FastAPI app at `/mcp`.
- **SQLite** WAL, `CORTEX_DB_PATH` / overseer DB path env-configured.
- **systemd** unit `cortex-gateway.service` on the Pi 5 (alongside the migrated
  overseer loop). `cloudflared` tunnel as a second unit.
- New repo/folder: `cortex-gateway/` (this folder). Git attribution per
  [[repos]].

## 10. Migration: .25 → Pi 5 (canonical takeover)

1. Provision Pi 5: NVMe SSD (canonical store lives here, **not** SD), 8GB+ RAM,
   active cooler, Python 3.11+, `cloudflared`.
2. Stop the overseer loop on `.25`; `rsync` `cortex.db` + `overseer.db` +
   `vault/` to the Pi 5 (final consistent copy).
3. Stand up the overseer loop + LLM router on the Pi 5 (it already calls out to
   OpenRouter/Gemini Flash - no local inference change). Verify a clean tick.
4. Bring up `cortex-gateway` against the Pi 5 DBs.
5. Repoint capture: `.25`/`.132`/the desktop Hub write THROUGH the Gateway
   (or sync up to it). `.25` demotes to capture client.
6. Cut DNS/tunnel over once a parity check passes.

**Parity check:** the BitTitan probe, run through the Gateway MCP `search` from
an external connector, returns ≥4 hits (today over LAN MCP it returned 0).

## 11. Open questions (for Tory)

1. **Token minting UX** - CLI only for now, or a small Hub admin screen?
2. **Per-connector sensitivity scoping** - do you want a "work" token that
   hides `personal`-tier content from ChatGPT/Grok used at work? (I'd default
   yes; it's cheap and matches your work/home split.)
3. **In-app AI chat** - Anthropic API directly from the app calling the same
   `cortex_*` tools, in Phase 3? Or defer to Phase 4?
4. **Capture write path** - do wearables/Hub write *through* the Gateway REST,
   or keep writing to a local DB on the Pi 5 that the Gateway shares? (Shared
   local DB is simpler; through-REST is cleaner for future multi-node.)
```
