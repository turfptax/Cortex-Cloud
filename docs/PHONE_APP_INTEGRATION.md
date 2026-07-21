# Cortex Gateway - Phone App Integration Spec

Handoff for the phone team. The Cortex Gateway is the internet-facing service
that fronts the Cortex corpus. This spec covers what the phone app needs to
build: (1) authenticate as the owner's trusted device via OAuth 2.1, and (2) the
Connections / approval screen for managing which AI connectors may read the
corpus. Backend is live; the phone work is the app side.

- Base URL: `https://cortex-gw-8fed.azurewebsites.net`
- All requests are HTTPS + JSON. Auth is `Authorization: Bearer <token>`.
- Companion specs: [MCP_OAUTH_AZURE_ENTRA_SETUP.md](MCP_OAUTH_AZURE_ENTRA_SETUP.md)
  (OAuth mechanics), [CONNECTOR_GRANTS_DESIGN.md](CONNECTOR_GRANTS_DESIGN.md)
  (the grant model this manages).

---

## 1. Authentication (the phone as a trusted OAuth client)

The phone authenticates with the SAME OAuth 2.1 + PKCE flow the AI connectors
use, but it is recognized as the owner's device and receives an elevated **`hub`**
scope (full corpus REST + connection management). A regular connector can never
obtain `hub`; the elevation is bound to the phone's redirect host, which the
gateway is configured to trust.

### Flow (authorization-code + PKCE, S256)

1. Generate a PKCE `code_verifier` + `code_challenge` (S256).
2. **Discover** (optional, values are stable):
   `GET /.well-known/oauth-authorization-server` returns `authorization_endpoint`,
   `token_endpoint`, `registration_endpoint`.
3. **Register** the client (RFC 7591 dynamic registration):
   `POST /oauth/register  { "client_name": "Cortex Phone", "redirect_uris": ["<PHONE_REDIRECT>"] }`
   -> `{ "client_id": "cli_..." }`. Register once and persist the `client_id`.
4. **Authorize** (opens a browser): `GET /oauth/authorize?response_type=code&client_id=<id>&redirect_uri=<PHONE_REDIRECT>&code_challenge=<challenge>&code_challenge_method=S256&state=<state>`.
   The gateway requires an interactive Microsoft Entra login (owner only), then
   shows a consent screen; on approve it 302-redirects to `<PHONE_REDIRECT>?code=<code>&state=<state>&iss=<issuer>`.
5. **Exchange**: `POST /oauth/token` (form-urlencoded) with
   `grant_type=authorization_code&code=<code>&redirect_uri=<PHONE_REDIRECT>&client_id=<id>&code_verifier=<verifier>`
   -> `{ "access_token": "...", "token_type": "Bearer", "scope": "hub", "expires_in": 2592000 }`.

The returned `scope` MUST be `hub`. If it is `connector:read`, the redirect host
is not configured as a hub host (see Config below) - surface that as a setup
error.

### `PHONE_REDIRECT`

Must be a URL whose **host only the phone app controls** (an app-claimed
universal/app link, e.g. `https://phone.<domain>/oauth/callback`). That host is
what the gateway trusts to mint `hub`, so it must not be registerable by anyone
else. Custom schemes (`cortex://`) are NOT accepted (https required). Confirm the
final redirect with the gateway owner so it can be added to config.

### Token lifetime

`hub` tokens last 30 days by default (`expires_in`). There is no refresh token
yet, so on `401 invalid or revoked token` the app re-runs the flow (steps 4-5;
the `client_id` is reused). Handle 401 by re-authenticating, not by erroring.

---

## 2. Corpus access (the phone's data API)

With a `hub` token the phone reads/writes the corpus over the existing `/v1`
REST surface (unchanged; same engine as the AI connectors' MCP tools):

| Method + path | Purpose |
|---|---|
| `GET /v1/search?q=&kinds=&days=&limit=` | Unified search (layered results). |
| `GET /v1/item/{token}` | One item's full content (e.g. `g:123`). |
| `GET /v1/recent?days=` | What changed recently. |
| `GET /v1/narratives?period=` | Temporal narratives. |
| `GET/POST /v1/journal` | Human journal. |
| `POST /v1/ingest` | Push content into the intake pipeline. |
| `GET/POST/PATCH /v1/projects`, `/v1/notes`, `/v1/people`, `/v1/time` | Relational spine. |

(These require `app` scope, which `hub` satisfies.)

---

## 3. Connections screen (managing AI connector access)

This is the new surface the phone team builds. When an external AI (Grok, Claude,
ChatGPT) connects, it lands DEFAULT-DENY (reads nothing) until the owner approves
it here. The owner picks an access level and, per connector, whether future
connections are auto-approved or prompt for confirmation.

### Model (what the UI shows)

Each connection has:
- **`level`**: `none` (metadata only, the default) or `full` (whole corpus).
  (`work`/`personal` tiers are a future addition; treat `level` as an open enum.)
- **`approval_policy`**: `ask` (a new/re-connection lands `pending` and must be
  confirmed) or `always` (auto-approved to its level).
- **`status`**: `pending` (needs the owner's confirmation), `active`, or `revoked`.

Suggested UI: a list with a badge for `status=pending` items ("N connections
need your approval"). Tapping a pending one shows who is asking (name, redirect
host, last-connected, last IP) with Approve (choose level) / Deny. Active
connections show their level + a toggle for always-approve, and a Revoke action.

### Endpoints (auth: `hub`/`app` token)

**`GET /v1/connections`**  (add `?status=pending` for just the ones awaiting approval)
```
200 { "connections": [ {
  "id": 12, "client_id": "cli_...", "name": "grok", "redirect_host": "grok.com",
  "level": "full", "approval_policy": "always", "status": "active",
  "first_connected_at": "2026-07-12T00:21:04Z",
  "last_connected_at":  "2026-07-12T05:10:00Z",
  "last_used_at":       "2026-07-12T05:12:33Z",
  "token_status": "active"        // active | revoked | none
} ] }
```

**`GET /v1/connections/{id}`** -> the same object; `404` if unknown.

**`POST /v1/connections/{id}/approve`** - confirm a pending connection or change
an active one's level.
```
Body:  { "level": "full", "always": true }   // level: "none" | "full"; always optional
200:   { ...updated connection... }           // status -> "active"
400:   { "detail": "invalid level: ..." }
404:   { "detail": "connection not found" }
```

**`POST /v1/connections/{id}/policy`** - set the confirmation policy.
```
Body:  { "approval_policy": "ask" }           // "ask" | "always"
200:   { ...updated connection... }
```

**`POST /v1/connections/{id}/revoke`** - disconnect: sets `status=revoked`,
`level=none`, and revokes the connector's tokens (it must re-OAuth to return).
```
200:   { "ok": true, "id": 12, "status": "revoked", "tokens_revoked": 1 }
```

All effects are immediate: the corpus read gate consults the grant on every read,
so an approve or revoke changes the connector's access on its next request.

### Notifications

v1: the app polls `GET /v1/connections?status=pending` to surface new requests.
Push notifications for a pending connection are a later enhancement.

---

## 4. Errors + conventions

- `401` - missing/invalid/expired/revoked token. Re-authenticate (section 1).
- `403` - token lacks the required scope. A `hub` token should not see this on
  these endpoints; if it does, the token was minted `connector` (redirect host
  not trusted as hub).
- `400` - bad request body (e.g. invalid `level`/`approval_policy`). The message
  is in `detail`.
- `404` - unknown connection id.
- `429` - rate limited; honor `Retry-After`.

---

## 5. Gateway config the owner must set (not the phone team, but coordinate)

- `GATEWAY_HUB_REDIRECT_HOSTS` = the phone's `PHONE_REDIRECT` host (e.g.
  `phone.<domain>`). Until this is set, the phone's token comes back
  `connector:read`, not `hub`. This is the switch that trusts the app.
- If a strict redirect allowlist is in force, the hub host is auto-trusted, so no
  separate allowlist entry is needed.

Give the phone team's chosen `PHONE_REDIRECT` to the gateway owner so this can be
configured, and the `client_id` from step 3 can be recorded.
