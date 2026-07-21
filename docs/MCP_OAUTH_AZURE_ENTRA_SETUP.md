# MCP OAuth 2.1 on Azure App Service + Entra ID - working setup

How the Cortex Gateway serves an OAuth 2.1 MCP connector flow (claude.ai,
ChatGPT, Grok) from Azure App Service with Entra ID as the human gate. This is
the runbook: the final working configuration plus every gotcha we hit getting
there (2026-07-11). Companion to [OAUTH_2_1.md](OAUTH_2_1.md) (the OAuth
security contract).

## The shape of it

Two auth layers, deliberately split:

- **Machine layer (our app):** bearer tokens + OAuth 2.1 authorization-code +
  PKCE. Discovery, dynamic registration, token exchange, and `/mcp` are all
  handled by the FastAPI app. No platform login on these.
- **Human layer (Azure Easy Auth / Entra):** exactly one path, the consent
  screen `GET /oauth/authorize`, is gated behind an interactive Entra login so
  only the resource owner can approve a connector. Enforced by
  `HumanLoginMiddleware` (redirects to `/.auth/login/aad` when the
  `x-ms-client-principal` header is absent), NOT by Easy Auth blocking paths.

A connector's happy path: `GET /mcp/` -> 401 with a `WWW-Authenticate`
resource-metadata pointer -> fetch discovery docs -> `POST /oauth/register` ->
open `GET /oauth/authorize` -> Entra login -> consent screen -> Approve ->
authorization code -> `POST /oauth/token` (code + PKCE verifier) -> access token
-> `/mcp/` with `Authorization: Bearer`.

## Final working configuration

**App Service** `cortex-gw-8fed` (Linux, Python 3.11, Oryx build):
- Startup: `python -m uvicorn cortex_gateway.app:app --host 0.0.0.0 --port 8000`
- App settings: `GATEWAY_OAUTH_ENABLED=1`,
  `GATEWAY_PUBLIC_URL=https://cortex-gw-8fed.azurewebsites.net`,
  `GATEWAY_OAUTH_TOKEN_TTL=86400`, `DB_URL=mssql+pymssql://...`,
  `SCM_DO_BUILD_DURING_DEPLOYMENT=true`. Optional:
  `GATEWAY_OAUTH_ALLOWED_REDIRECTS` (lock-down), `GATEWAY_OAUTH_ALLOW_WRITE`
  (default off = read-only connectors), `GATEWAY_DEBUG` (tracing).

**Easy Auth (authV2), single-tenant:**
- AAD provider `cortex-gw-auth`, `signInAudience: AzureADMyOrg`, issuer
  `https://login.microsoftonline.com/<tenant>/v2.0` (openmuscle.org tenant).
- `unauthenticatedClientAction: AllowAnonymous` (the app handles auth; Easy Auth
  only injects `x-ms-client-principal` for logged-in users). `requireAuthentication: true`.
- `appRoleAssignmentRequired: false` (no per-user assignment; any tenant account works).
- Do NOT enable Easy Auth on `/mcp` - it breaks MCP discovery and bearer keys.

**Connector callbacks (the redirect allowlist values):**
- Claude: `https://claude.ai/api/mcp/auth_callback`
- ChatGPT: `https://chatgpt.com/connector_platform_oauth_redirect`
- Grok: `https://grok.com/connectors-oauth-exchange-code/`
- Connector URL to hand out: `https://cortex-gw-8fed.azurewebsites.net/mcp/`
  (with trailing slash - see gotcha 1).

## Gotchas we hit (and the fixes)

1. **`/mcp` (no slash) 307-redirects to `http://`.** uvicorn behind Azure TLS
   termination doesn't know it's on https, so Starlette's mount redirect
   downgrades the scheme and clients refuse it. Fix: always hand out `/mcp/`
   with the trailing slash (it answers 401 directly, no redirect).

2. **OAuth discovery burst got 429'd.** The anon rate-limit bucket was keyed on
   `request.client.host`, which behind Azure is the platform proxy IP
   (169.254.x.x) for every caller - one shared global bucket - and the discovery
   docs weren't exempt. A connector fetches several discovery docs at once and
   drained it. Fix: key the anon bucket on the forwarded client IP, and exempt
   `/.well-known/*` from rate limiting (like `/health`).

3. **RFC 9728 path-suffixed metadata.** Clients derive the resource-metadata URL
   from the resource id and probe `/.well-known/oauth-protected-resource/mcp`,
   not just the bare path. Serve both, and advertise the suffixed one in the
   `/mcp` 401 challenge.

4. **Entra login round-trip dropped the OAuth query.** The login redirect used
   only `request.url.path` as `post_login_redirect_uri`, so after login the
   consent endpoint came back with no `client_id`/`redirect_uri`/`code_challenge`
   and 422'd. Fix: carry the full path+query (url-encoded) through the round-trip.

5. **Easy Auth 403s the authenticated consent POST.** The consent screen (a GET)
   rendered fine, but the Approve POST never reached the app - Azure Easy Auth
   returns 403 for an authenticated POST inside a connector's popup (POST +
   cookie in a third-party/popup context). Fix: make Approve a **GET** carrying a
   single-use, request-bound **consent nonce** (`oauth_consent` table). GETs pass
   through Easy Auth; the nonce keeps the state-changing GET safe from
   forgery/replay. This also delivered the consent-nonce CSRF hardening.

6. **Single-tenant means the right account only.** `signInAudience` is
   `AzureADMyOrg`, so only the app-tenant (openmuscle.org) account is authorized;
   a different Entra identity gets an Easy Auth 403 after login. Log in with the
   app-tenant account (an incognito window forces a clean account picker instead
   of silent SSO with the wrong one).

7. **Warm restart does not re-read app settings.** `get_settings()` is
   `lru_cache`d per process, and `az webapp restart` (warm) can serve a stale
   process. Changing an env var that config caches (OAuth enable, allowlist,
   debug, TTL) needs a cold `az webapp stop` then `az webapp start`.

8. **Never change an app setting and deploy at the same time.** Both restart the
   site; overlapping them races the container swap, corrupts the deploy (502),
   and crash-loops the site into App Service's cold-start circuit breaker. Do one,
   let `/health` return 200, then the other. After a deploy that *looks* failed,
   verify real state (`curl /health`, `az webapp show --query state`,
   `/home/LogFiles/*_docker.log` via a Kudu AAD token) before rolling back - the
   `az webapp deploy --async false` CLI can time out while the container is still
   coming up.

## Operating it

- **Enable / disable OAuth:** `GATEWAY_OAUTH_ENABLED` (cold restart).
- **Lock down clients:** set `GATEWAY_OAUTH_ALLOWED_REDIRECTS` to the exact
  callbacks above; registration and authorize then reject any other redirect.
- **Read vs write:** connectors are read-only by default; set
  `GATEWAY_OAUTH_ALLOW_WRITE=1` to let them request `connector:write`.
- **Tokens are short-lived (24h)** and re-auth via the flow; there is no refresh
  token yet.
- **What's connected:** the `connector_connections` table in the canonical DB
  records every successful connector authentication (client, name, scope, tier,
  source IP, time). `GATEWAY_DEBUG=1` adds an `oauth_trace` line per request
  (identity included) to the Azure log stream for troubleshooting.

## Connecting Claude (two different surfaces)

Claude has TWO connector paths with DIFFERENT redirect URIs; support both.

- **claude.ai web / Desktop / mobile / Cowork (hosted):** redirect
  `https://claude.ai/api/mcp/auth_callback` (already in the allowlist). Add the
  connector in the claude.ai UI with the server URL
  `https://cortex-gw-8fed.azurewebsites.net/mcp/`; it runs the same OAuth flow as
  any connector. The MCP data connection comes from Anthropic's cloud
  (egress `160.79.104.0/21`), so keep discovery + `/mcp` publicly reachable (we
  do - Easy Auth is AllowAnonymous and only gates the human consent screen).
- **Claude Code (native CLI/app):** uses RFC 8252 **http loopback** callbacks
  (`http://127.0.0.1:PORT/callback` and `http://localhost:PORT/callback`) with
  ephemeral ports, so an https-only exact allowlist blocks it. Enable
  `GATEWAY_OAUTH_ALLOW_LOOPBACK=1` (cold restart). Then:
  ```
  claude mcp add --transport http cortex https://cortex-gw-8fed.azurewebsites.net/mcp/
  claude            # start a session
  /mcp              # select cortex -> Authenticate -> browser consent
  ```
  Loopback is safe: the authorization code is delivered only to the user's own
  machine (a remote attacker cannot intercept 127.0.0.1), PKCE protects it, and
  the Entra-gated consent still requires the app-tenant login. Matched
  port-agnostically at both registration and authorize.

Both surfaces send S256 PKCE and form-urlencoded token requests (we support
both), and bootstrap via the `WWW-Authenticate` resource-metadata header +
`/.well-known/*` discovery. Claude does not require the OpenAI search/fetch
tools, but keep them (ChatGPT does).

## Tool discoverability - what a connecting LLM sees

Once authenticated, a connector calls `initialize` then `tools/list`. What it
gets back is the whole discovery surface, so it is written for the model, not
just for humans. Conventions we follow (MCP spec 2025-11-25):

- **Server `instructions`** (set on `FastMCP(...)`): a short "what this server
  is + when to use each tool" guide. This is the model's first orientation;
  keep it a tool-selection map, not marketing.
- **Per-tool `title`**: a human-readable display name (e.g. "Search Cortex
  memory") shown in connector UIs alongside the machine `name`.
- **Behavioral annotations** (`ToolAnnotations`) on every tool, so clients can
  auto-approve safe reads and confirm on writes:
  - reads (`search`, `fetch`, `cortex_search`, `cortex_read`, `cortex_recent`)
    → `readOnlyHint=true`, `openWorldHint=false` (closed corpus); the
    deterministic ones also `idempotentHint=true`.
  - `cortex_ingest` (write) → `readOnlyHint=false`, `destructiveHint=false`
    (additive, never deletes), `openWorldHint=false`.
  - Annotations are hints, not security; the real controls are scope +
    sensitivity gating. Unannotated tools default to destructive + open-world,
    the most restrictive, so setting them improves both UX and confidence.
- **Descriptions** are action-oriented and say WHEN to use the tool and what the
  ids/tokens mean (e.g. `g:123`), because the model picks tools from these.
- **The OpenAI-compatible `search`/`fetch` pair is mandatory**: ChatGPT
  connectors reject any MCP server lacking them with OpenAI's exact result
  shape (`{results:[{id,title,text}]}` / `{id,title,text,metadata}`). Claude and
  Grok use them too, with the richer `cortex_*` tools layered on top.

Defined in `cortex_gateway/mcp_server.py`; guarded by `tests/test_mcp_discovery.py`.
Discovery is unauthenticated only at the metadata layer (`/.well-known/*`); the
tool list itself requires a valid bearer token.
