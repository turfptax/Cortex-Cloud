# OAuth 2.1 on the Cortex Gateway

The Gateway is the single internet-facing bridge into the owner's private corpus, so
its token-minting surface is the highest-value target in the system. This note
records the OAuth 2.1 best practices the bridge follows, why, and what is
deliberately deferred. It is the reference for anyone touching `oauth.py`.

Background: a 25-agent security audit on 2026-06-23 rated the OAuth server
CRITICAL (anyone on the internet could mint a corpus token). Those criticals are
fixed and deployed (commit `bc3ac41`). Full report:
`_audit_backups/GATEWAY_SECURITY_AUDIT_2026-06-23.md`.

## What this surface is for

OAuth 2.1 + PKCE exists for consumer-UI custom connectors (claude.ai, the
ChatGPT app) that will not accept a pasted bearer token and instead run an
authorization-code flow. Everything else (the phone app, API connectors like
Grok's custom MCP) uses pre-shared bearer keys and never touches OAuth.

Single resource owner: there is exactly one user (the owner), so the consent screen
is an approve button gated behind an interactive Entra login, not a signup.

## Best practices followed

- **Disabled by default.** OAuth is a public token-minting surface, so it stays
  off unless `GATEWAY_OAUTH_ENABLED=1`, and while it is off
  `POST /oauth/register` and `POST /oauth/token` return 404. Turn it on only
  when wiring a real connector. Any instance running a claude.ai/ChatGPT
  connector or the phone app has it on. (`config.py`,
  `oauth.py:_require_enabled`)
- **Authorization code + PKCE, S256 only.** Plain PKCE and any non-S256 method
  are rejected. The verifier is checked with a constant-time compare. Public
  clients, no client secret. (`oauth.py:_verify_pkce`, `authorize_form`)
- **The consent screen requires a human login.** `/oauth/authorize` is in
  `_HUMAN_PATHS`, so Entra login is enforced before any code is issued. An
  anonymous caller cannot mint a code even with a self-registered client. This
  is what closes the audit's zero-click critical. (`app.py:_HUMAN_PATHS`)
- **Scope can never escalate.** `_clean_scope` intersects the request against
  `{connector:read, connector:write}` at BOTH authorize and token, so `admin`
  and `app` can never reach an OAuth-minted token. It accepts comma- or
  space-delimited input and returns the RFC 6749 space form for the response;
  the mint site stores the comma form that `auth._lookup` expects, and
  `_lookup` splits on either delimiter as a backstop. (`oauth.py:_clean_scope`,
  `oauth.py:token`, `auth.py:_lookup`)
- **Exact, https-only redirect URIs.** Registration rejects non-https and
  malformed URIs; the token exchange rebinds `client_id` + `redirect_uri`
  exactly against the stored code. (`_valid_redirect`, `_known_redirect`)
- **Single-use codes, atomically.** Codes live 60s and are consumed with a
  guarded `UPDATE ... WHERE used = 0` that returns a row count; only the writer
  that flips `0 -> 1` mints. This closes the replay race under scale-out
  (`--max-replicas` > 1). (`oauth.py:token`, `db.execute_write`)
- **Short-lived access tokens, long-lived refresh tokens.** OAuth-minted access
  tokens expire after `GATEWAY_OAUTH_TOKEN_TTL` seconds (default 24h) and the
  token response carries `expires_in`. Immortal tokens are opt-in only
  (TTL <= 0), and they also suppress refresh-token issuance, since there is
  nothing to refresh. Keep the access TTL SHORT: it is the exposure window for a
  captured access token, and with the refresh grant below a short TTL costs a
  connector nothing because it renews itself. If a deployment lengthened this as
  a workaround for the pre-refresh "connector dies daily" problem, put it back to
  the default. The long-lived credential is now the refresh token, which unlike a
  bare access token is rotated on every use and revocable.
- **Refresh tokens, rotated, with reuse detection.** `grant_type=refresh_token`
  is supported (`refresh.py`, `oauth.py:_refresh_grant`), so a connector renews
  itself without a human at a browser. Redemption is single-use and atomic
  (`UPDATE ... WHERE used_at IS NULL` returning a row count) and mints a
  successor in the SAME `family_id`. Presenting an already-used token means two
  parties hold it, so the entire family AND every access token for that client
  are revoked. Only the hash is stored. Lifetime is
  `GATEWAY_OAUTH_REFRESH_TTL` (default 90d). A refresh is NOT a new connection:
  it deliberately does not call `grants.upsert_on_connect`, because on an `ask`
  policy that resets an active grant to pending and would push the connector back
  into the approval queue on every rotation.
- **A revoked connection cannot refresh back to life.** Owner revoke
  (`grants.revoke`), startup dedupe (`grants.dedupe_connections`) and single-key
  revoke (`connectors.revoke`) all call `refresh.revoke_for_client`. As defense in
  depth, `refresh.redeem` independently re-checks `connector_grants.status` and
  refuses if the connection was revoked.
- **Authentication failures are recorded durably.** A 401 on `/mcp` carrying a
  token, and every token-endpoint rejection, writes an `auth_failures` row
  (reason, path, client, non-secret key prefix, source IP, time), readable at
  `GET /admin/auth-failures`. The Container App environment is created without a
  log destination, so without this a connector failure left no trace that
  survived a restart. (`authlog.py`)
- **Reflected values escaped + strict CSP.** Every value echoed into the
  consent HTML is `html.escape`'d and served under
  `default-src 'none'; form-action 'self'; base-uri 'none'`, so a crafted
  `state` / `redirect_uri` cannot script the consent origin. Redirect query
  values (`state`, `iss`) are URL-encoded. (`oauth.py:authorize_form`)
- **Issuer identification (RFC 9207).** The authorization response echoes `iss`
  and discovery advertises `authorization_response_iss_parameter_supported`, so
  a client can detect an authorization-server mix-up / code-injection swap.
- **Tokens are audience-capped at mint.** OAuth tokens are hardcoded to
  `max_tier=internal` and `kind=oauth`, so even a valid connector token cannot
  reach confidential/restricted content or the admin/app REST spine.
- **Discovery is standards-based.** RFC 8414 authorization-server metadata and
  RFC 9728 protected-resource metadata are served so OAuth-capable connectors
  self-configure; a 401 on `/mcp` advertises the resource metadata URL.
- **Consent via GET + single-use nonce.** The consent screen (Entra-gated GET)
  mints a single-use nonce bound to client_id+redirect_uri+scope+code_challenge
  (`oauth_consent`); Approve is a GET that redeems it and issues the code. This
  is the CSRF hardening (a forged/replayed approval can't succeed) and it works
  around Easy Auth 403ing the authenticated consent POST in a connector popup.
- **Minted scope is read-only by default; effective writes gated by grant.**
  OAuth mints `connector:read` only; the `connector:write` scope string is added
  only when `GATEWAY_OAUTH_ALLOW_WRITE` is set (this now matters mainly for
  static CLI tokens). The minted scope no longer decides whether a connector can
  write. Effective writes are gated by `grants.can_write()`, which allows a write
  for the owner (`app`), for a token carrying `connector:write`, OR for any
  connection the owner approved to `full`. So an approved connection can write
  regardless of the ALLOW_WRITE flag; a leaked connector token writes only if its
  connection was approved to `full`, and even then writes are additive, never
  destructive. (`grants.can_write`, `oauth.py:_clean_scope`)
- **Connections recorded in the corpus.** Every successful token issuance writes
  a `connector_connections` row (client, scope, tier, source IP, time) to the
  canonical store, a durable record independent of the Azure log stream.
- **Registration + authorize audit log.** Every `/oauth/register` and consent
  `/oauth/authorize` attempt emits one structured `oauth_audit` line to the app
  log stream (allowed = INFO, blocked = WARNING): timestamp, outcome, caller IP,
  client, attempted redirect_uri, block reason, and requested-vs-granted scope
  (so a stripped `admin`/`app` escalation attempt stays visible). No secrets are
  logged (never the authorization code, verifier, or token). Lightweight: no DB
  write. (`oauth.py:_audit`)
- **Trusted-client redirect allowlist (opt-in).** When
  `GATEWAY_OAUTH_ALLOWED_REDIRECTS` is set, dynamic registration AND the
  authorize flow accept ONLY those exact https redirect URIs (the claude.ai /
  chatgpt.com connector callbacks). This is the primary consent-phishing
  defense: an attacker cannot register a redirect that would deliver the code to
  their own endpoint, and a client grandfathered in before lockdown still cannot
  authorize with a non-listed redirect. EMPTY (unset) keeps registration open so
  a rollout can test real connectors, observe the exact callbacks they use, then
  lock down. (`config.py:oauth_allowed_redirects`, `oauth.py:_redirect_allowed`)

## Deliberately deferred (with reasons)

- **Sensitivity ceiling still fails open on untagged rows.** This is a corpus
  gating decision (`corpus_service`/`sensitivity.py`), not an OAuth flow issue,
  and pairs with cortex-core tagging. Tracked in the audit report and project
  memory, awaiting the owner's decision. Lower urgency while OAuth is off and only
  manual trusted tokens exist.

## Enabling OAuth for a real connector

1. Set `GATEWAY_OAUTH_ENABLED=1` and `GATEWAY_PUBLIC_URL=https://<domain>`.
2. The token lifetimes have working defaults and normally need no setting:
   `GATEWAY_OAUTH_TOKEN_TTL` (access, default 86400 = 24h) and
   `GATEWAY_OAUTH_REFRESH_TTL` (refresh, default 7776000 = 90d). Do not raise the
   access TTL and do not set it to 0; see the access-token bullet above.
3. **Rollout, then lock down** with the trusted-client allowlist:
   - Phase 1 (test): leave `GATEWAY_OAUTH_ALLOWED_REDIRECTS` unset, connect the
     real connectors, and read back the exact `redirect_uris` they registered
     (from the `oauth_clients` table) to confirm the callback strings.
   - Phase 2 (lock down): set `GATEWAY_OAUTH_ALLOWED_REDIRECTS` to those exact
     https callbacks (space/comma separated), e.g.
     `https://claude.ai/api/mcp/auth_callback https://chatgpt.com/connector_platform_oauth_redirect`.
     Registration and authorize then reject every other redirect. NB: settings
     are read once per process (`get_settings()` is cached), so a change only
     takes effect on a fresh process. On Container Apps an env-var change creates
     a new revision, which satisfies this by itself.
4. Confirm live: `POST /oauth/register` accepts a listed callback (201) and
   rejects an unlisted one (400); `GET /oauth/authorize` redirects to Entra
   login; a full register -> authorize -> token dance with
   `scope=connector:write,admin` yields only `connector:write`.

Grok note: Grok's MCP connection does not appear to use a standard redirect
callback, so it is not in the allowlist; revisit if Grok adopts the OAuth flow.

## Debugging a connector flow (`GATEWAY_DEBUG`)

**Read this first: your instance probably keeps no server logs.** `deploy.sh`
creates the Container App environment without a log destination, so
`appLogsConfiguration.destination` is null and nothing is retained. The live tail
below shows only what the CURRENT replica has emitted since it started, and a
restart erases it. For authentication problems specifically, use the durable
`auth_failures` table via `GET /admin/auth-failures` instead; it survives
restarts. To get real platform logs, recreate the environment with
`--logs-destination log-analytics` and a workspace.

Set `GATEWAY_DEBUG=1` (a new revision applies it) to turn on exhaustive tracing,
then tail the running replica with
`az containerapp logs show -g <rg> -n <app> --container gateway --follow`.
Two extra streams appear:

- `oauth_trace` - one line per request: method, path, query, status, whether
  Easy Auth authenticated it (`authed`) and the decoded identity
  (`idp`/`name`/`upn`/`tid` - the account + tenant Easy Auth injected), client
  IP, user-agent, and duration. This is how you confirm which Entra identity a
  403/consent request carried, and see the full connector handshake sequence.
- `oauth.*` DEBUG lines - the token exchange logs each check
  (grant_type/client/redirect/expiry/PKCE) and why an `invalid_grant` was
  returned, without ever logging the code or verifier.

Turn it OFF (`GATEWAY_DEBUG` unset, cold restart) once a flow works: the trace
logs identity claims (PII) and is meant for single-user debugging, not steady
state. Re-enable anytime.

Tests: `tests/test_oauth.py`.
