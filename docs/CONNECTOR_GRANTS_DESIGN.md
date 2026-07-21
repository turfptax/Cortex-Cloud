# Connector Access Grants - Design

Status: IMPLEMENTED 2026-07-12 (v1: none/full + ask/always confirmation). The
DB `connector_grants` model, the read-gate enforcement, and the
`/v1/connections` endpoints are live; the phone approval screen is the phone
team's build. Replaces the sensitivity-ceiling default as the primary control
for what an external AI connector can read.
Companion to [MCP_OAUTH_AZURE_ENTRA_SETUP.md](MCP_OAUTH_AZURE_ENTRA_SETUP.md)
(how connectors authenticate) and [OAUTH_2_1.md](OAUTH_2_1.md).

## Why

The tier ceiling asked "how sensitive is each row" and failed OPEN when a row
was untagged (security audit 2026-07-12, Finding 1: a `connector:read` token
read the full untagged corpus). This model asks "do I trust this connection,
and with what," and is fail-closed by construction: a connection sees nothing
until the owner grants it, regardless of how rows are tagged.

Clean split: OAuth + Entra consent proves WHO is connecting (authentication);
the grant decides WHAT they get (authorization), and the owner controls it from
the phone app.

## Scope

- **v1 (this design): two access levels + a confirmation flow.**
  - Levels: `none` (metadata only) and `full` (everything, including
    uncategorized content).
  - Per-connector confirmation policy: `ask` (owner confirms each new
    connection) vs `always` (auto-approve known connector).
- **v2 (future session):** `work` / `personal` tiering via row categories. The
  level field is designed to extend to more values without an API break.

## Model

### Access level (what a connection can read)

| Level | Reads |
|---|---|
| **`none`** (default) | Metadata only. NO corpus content. Tools list + a "pending approval" marker. |
| **`full`** | The whole corpus, including uncategorized content, subject only to the existing raw-layer rule (raw `imported_sessions`/`files` still never leave via a connector). |

Uncategorized content is treated as `full` at the `full` level (per owner
decision, v1). Non-connector callers (the phone `app` scope, or the phone once
it uses OAuth as a trusted connector) are unaffected and keep full access.

### Confirmation policy (whether each new connection is auto-approved)

The owner wants to *know exactly which connection is requesting access* and
choose, per connector, between auto-approve and confirm-each-time. So each
connector carries an `approval_policy`:

| Policy | On a NEW connection (a new OAuth token minted for this connector) |
|---|---|
| **`ask`** (default) | The connection enters `pending`, the owner is notified, and it stays at `none` until the owner confirms it in the app. |
| **`always`** | Auto-approved to the connector's remembered `level`, no prompt. |

A "connection" is one connector identity; a "connection attempt" is a token
mint. The pending record surfaces exactly who is asking: name, redirect host,
client_id, source IP, and time.

### Status (per connection)

`pending` (awaiting owner confirmation) → `active` (confirmed, reads at `level`)
→ `revoked` (disconnected). Denying a pending connection sets `revoked`.

### Enforcement (read time)

Single choke point, already present: `corpus_service._gate_decision()` (every
MCP read: `search`, `fetch`, `cortex_recent`). A connector reads content only if
its connection is `active` AND `level == full`; otherwise `withheld`. The lookup
is per-read, so a level change or revoke takes effect immediately.

## Data model

### New table: `connector_grants`

One row per connector identity (OAuth `client_id`). Durable owner state; survives
the 24h token re-mint; the object the app lists and edits.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | Stable id the app addresses. |
| `client_id` | string, unique | OAuth `client_id`. |
| `name` | string | Display name (`oauth_clients.client_name`). |
| `redirect_host` | string | e.g. `grok.com` (for display + stable identity). |
| `level` | string | `none` \| `full` (v1). Default `none`. |
| `approval_policy` | string | `ask` \| `always`. Default `ask`. |
| `status` | string | `pending` \| `active` \| `revoked`. Default `pending`. |
| `first_connected_at` | datetime | First seen. |
| `last_connected_at` | datetime | Most recent token mint. |
| `granted_at`/`granted_by` | datetime/string, null | Last approve + owner oid. |
| `updated_at` | datetime | |
| `note` | string, null | Optional owner note. |

### Change to `gateway_tokens`

Add `client_id` (string, nullable), set for `oauth` tokens, so the read gate
resolves the grant directly.

### Lifecycle wiring

On each successful token mint (`oauth.token()`): upsert the `connector_grants`
row. New client_id -> create `pending`/`ask`/`none`. Known + `always` ->
refresh `last_connected_at`, keep `active`. Known + `ask` -> set `pending` again
and re-notify (a re-connection is a fresh consent event).

## Interim (shipped 2026-07-12, before the table lands)

Config `GATEWAY_CONNECTOR_FULL_HOSTS` (redirect-host allow-list, default empty =
deny all connectors). `_gate_decision` withholds everything from a connector
whose registered redirect host is not listed. Set to `grok.com` in prod: Grok
stays `full`, every other/unapproved connector reads nothing, app/phone
unaffected. This closes the Finding 1 leak now; the DB model + endpoints below
REPLACE it.

## API (owner-facing, for the phone app)

Prefix `/v1/connections`. Auth: `app` scope (the phone's token = the owner's
trusted device). JSON. All effects immediate.

### `GET /v1/connections[?status=pending]`

List connections (optionally just those awaiting confirmation, for a badge).

```
200 OK
{ "connections": [
  { "id": 12, "client_id": "cli_...", "name": "grok",
    "redirect_host": "grok.com",
    "level": "full", "approval_policy": "always", "status": "active",
    "first_connected_at": "2026-07-12T00:21:04Z",
    "last_connected_at":  "2026-07-12T05:10:00Z",
    "last_used_at":       "2026-07-12T05:12:33Z",
    "last_source_ip": "160.79.104.10", "token_status": "active" } ] }
```

### `POST /v1/connections/{id}/approve`

Confirm a `pending` connection (or change an active one's level). Sets
`status=active` and `level`; if `always=true`, also sets `approval_policy=always`.

```
Request:  { "level": "full", "always": true }   // level: "none"|"full"
200 OK:   { ...updated connection... }
400/404
```

### `POST /v1/connections/{id}/policy`

Set the confirmation policy independently.

```
Request:  { "approval_policy": "ask" }          // "ask" | "always"
200 OK:   { ...updated connection... }
```

### `POST /v1/connections/{id}/revoke`

Disconnect: `status=revoked`, `level=none`, and revoke the outstanding token(s)
(`gateway_tokens.revoked_at`) so the connector must re-OAuth to return.

```
200 OK:   { "ok": true, "id": 12, "status": "revoked", "tokens_revoked": 1 }
```

Notifications (a phone push when a `pending` connection appears) are a follow-on;
v1 can poll `GET /v1/connections?status=pending`.

## The phone app itself (hub scope - implemented, inert by default)

The phone authenticates with the same OAuth 2.1 + PKCE flow as connectors, but a
client whose redirect host is in `GATEWAY_HUB_REDIRECT_HOSTS` is minted the
elevated **`hub`** scope (implies `app`: full REST + connection management),
NOT a connector scope. It is therefore not grant-gated and always has full
access. A self-registering connector can never obtain `hub` (verified by test:
requesting `hub`/`app`/`admin` still yields only `connector:read`), and the
mechanism is INERT until the owner sets the phone's redirect host. Security rests
on that host being an app-claimed URL only the phone controls. `hub` tokens last
30d (`GATEWAY_HUB_TOKEN_TTL`; re-auth on expiry, no refresh token yet). Full phone
build spec: [PHONE_APP_INTEGRATION.md](PHONE_APP_INTEGRATION.md).

## Migration

On rollout of the DB model, every existing connection defaults to `none` /
`pending` (no auto-elevation). Grok is (re)approved once from the app. The
interim `GATEWAY_CONNECTOR_FULL_HOSTS` is removed at that point.

## Open decisions / config

- API auth scope: specced as `app` (phone). A leaked `app` token already has
  full corpus access, so this adds no exposure; can be `admin` if isolation is
  preferred.
- Grant keyed by `client_id`; a connector that re-registers (new DCR client_id)
  appears as a NEW `pending` connection to re-confirm. With `approval_policy`
  this is the intended vetting behavior, but note the UX; the stable
  `redirect_host` is shown so the owner recognizes it.

## Out of scope (future)

- `work` / `personal` category tiering under `level` (v2).
- Per-project or per-kind grants; time-boxed / one-session grants.
- Push notifications for pending connections.
