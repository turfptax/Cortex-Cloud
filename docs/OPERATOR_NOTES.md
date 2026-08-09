# Operator notes

Release notes for people already running a Cortex-Cloud instance. Each entry says
what changed, what happens by itself, and what needs you to do something. Newest
first.

If you are standing up a new instance instead, start from the README; you get all
of this by default and nothing below applies to you.

Companion docs: [OAUTH_2_1.md](OAUTH_2_1.md) for the token model,
[MCP_OAUTH_AZURE_ENTRA_SETUP.md](MCP_OAUTH_AZURE_ENTRA_SETUP.md) for the connector
runbook, [CONNECTOR_GRANTS_DESIGN.md](CONNECTOR_GRANTS_DESIGN.md) for the approval
model.

## 2026-08-09: settings polish - model combobox and paste-a-link channels

**Nothing to do.**

Two owner-feedback fixes on the day-old Overseer settings card:

- The model picker is a real combobox now: clicking a field shows the whole
  OpenRouter catalog (scrollable, with a count footer), typing replaces the
  current id and filters live, and a no-match state says plainly that free
  text is fine. Before this, the list was filtered by the field's existing
  value, so a populated field appeared to only know three models.
- The YouTube channels list accepts a pasted link: channel URL, @handle,
  video URL, or bare UC id. A new core endpoint
  (`POST /plugins/overseer/settings/resolve-youtube`) resolves it using the
  channel page's `externalId` (the first `"channelId"` in YouTube HTML often
  belongs to a FEATURED channel, a 2026-06 lesson), verifies the id against
  the same RSS feed the ingester polls, and appends a ready
  `persona:channel_id` line. Duplicates are detected by channel id.

## 2026-08-08: the MCP surface explains itself, and every pillar got modify/remove

**Nothing to do.** New tools appear to connectors on their next tools/list;
existing tools keep their contracts.

Connecting AIs had no way to see the schema without failing at it first (the
reference owner's voice-session logs caught an AI guessing `text` for
cortex_ingest's `content`). Three things changed:

- **cortex_intro**, a run-this-first tool: the live tool roster with call
  shapes (generated from the registry at call time, so it cannot drift),
  whether the calling connection can write, the per-pillar write contract
  (add / modify / remove), the token legend, and the common gotchas.
  `brief=true` adds the owner-context brief.
- **The surface is now CRUD-complete, softly**: cortex_note_update corrects
  AI-written notes (owner captures stay read-only over MCP),
  cortex_org_upsert creates/edits/retires organizations (is_active=0),
  cortex_rule_update refines or retires a rule by title, cortex_task_update
  can retitle. Every remove is a status flip, never deletion. People remains
  owner-only.
- **A drift guard in CI**: test_mcp_discovery fails any change that adds a
  tool without naming it in the server instructions, and any write tool
  missing from the write contract. The tool list can no longer silently rot.

cortex_ingest also tolerates `text` as an alias for `content` and answers a
missing body with the full schema instead of a bare error.

## 2026-08-08: the overseer's dials moved onto the web Settings page

**Nothing to do.** Behavior is unchanged until you touch a dial.

The Settings page grew an Overseer card. It edits a per-instance overlay on
`plugin.toml`: the main model, the per-task model routing, the loop cadence and
budgets, and the git/YouTube ingest lists. Overrides are stored in your corpus
database (`overseer_state` key `runtime_settings_overrides`), win over the
manifest at read time, and apply on the next LLM call or tick with no restart
and no redeploy. `plugin.toml` remains the documented default set that ships
with the code.

Two things worth knowing:

- The model picker autocompletes from OpenRouter's public catalog with per-1M
  pricing, but free text is accepted, so a model that shipped an hour ago works.
- "reset to default" per dial returns you to the manifest value; when the last
  override is removed the storage row is deleted outright.

Heads-up for a later release: the git/YouTube ingest lists in `plugin.toml` are
still the reference instance's own. Now that they are editable per-instance,
a future release may blank those repo defaults. Set yours in Settings and the
change will not affect you.

## 2026-08-01: a corrupt gateway.db replica can stop your instance booting

**If you run an instance, pull master.** This was a ~2 day outage caused by code
in `deploy/`, not by anything in the Azure account.

### What you would see

The app accepts connections and returns nothing. Every revision wedges at
`NotRunning - "System Identity Container is still running."` with all containers
`Started: False` and 0 restarts. The revision still reports `Healthy`. Rebuilding
does not help. The scheduled tick job fails on every run.

### Cause

`deploy/litestream-restore.sh` ran `set -e` over all three databases. If
`gateway.db`'s replica has a broken WAL generation, litestream exits 1, the init
container dies, and the four main containers never start. The corpus is fine the
whole time. The least valuable database takes the product down.

### Fix

Already on master: the restore step now aborts only for databases that cannot be
rebuilt (`cortex.db`, `overseer.db`) and warns-and-continues for `gateway.db`.
Pull and redeploy.

If you are stuck right now, delete just the `gateway.db/` prefix from your
litestream blob container. Leave `cortex.db/` and `overseer.db/` alone. The app
recreates `gateway.db` on boot and connected clients reconnect once.

### Two things worth doing on your own deployment

- **`deploy.sh` creates the managed environment with no log destination**, so you
  retain no server logs at all. Attaching a Log Analytics workspace is what
  finally exposed this error, after three wrong hypotheses. Create the
  environment with `--logs-workspace-id` / `--logs-workspace-key`.
- **Alert on repeated tick-job failure.** It was the earliest signal here, about
  a day before anyone noticed, and nothing surfaced it.

Full write-up: [POSTMORTEM_2026-08-01_restore_outage.md](POSTMORTEM_2026-08-01_restore_outage.md).

## 2026-07-27 (commit a3103d2): OAuth refresh tokens + durable auth-failure log

### Why it shipped

OAuth access tokens expired after 24 hours and the gateway supported only the
`authorization_code` grant, so a connector had no way to renew itself and renewal
meant re-running the browser consent flow by hand. On the reference instance this
looked like the Claude connector breaking roughly once a day; the token table
showed four full re-authorizations of the same client in five days.

### What is automatic

Nothing in this list needs you.

- **The two new tables create themselves.** `oauth_refresh_tokens` and
  `auth_failures` are added by `db.init_schema()`, which `create_app()` runs at
  startup. They are new tables rather than new columns, so `create_all()` is
  sufficient and there is no migration to run. Existing rows are untouched.
- **Discovery updates itself.** `/.well-known/oauth-authorization-server` now
  advertises `grant_types_supported: ["authorization_code", "refresh_token"]`, and
  `POST /oauth/register` returns the same pair. Compliant connectors pick this up
  on their own.
- **Refresh tokens are issued from now on.** Every token exchange that produces an
  expiring access token also returns a `refresh_token`.
- **Revocation got stricter on its own.** Revoking a connection, revoking a
  connector key, and the startup dedupe pass now all revoke that client's refresh
  tokens too, and a refresh is independently refused if the connection's grant is
  revoked. You do not need to do anything to get this.

### What you must do

1. **Redeploy.** Pull master and redeploy so the gateway is running this code.

2. **Expect one last manual reconnect per existing connector.** This is the part
   people get wrong, so it is worth being precise. Refresh tokens are only minted
   at the token endpoint. A connector that authorized BEFORE this deploy holds an
   access token with no refresh token attached, and upgrading does not
   retroactively give it one. That connector keeps working until its current
   access token expires, then fails once and needs you to reconnect it in the
   normal way. From that reconnect onward it renews itself and should not need you
   again.

3. **If you lengthened `GATEWAY_OAUTH_TOKEN_TTL`, put it back.** Raising the access
   token lifetime was the only workaround available before refresh existed, and
   the reference instance had it at 30 days. That is now the wrong setting: the
   access token is the credential that gets captured, and the refresh token is the
   one that is long lived, rotated on every use, and revocable. Remove the override
   (or set it back to `86400`) so access tokens are short again. Connectors will
   not notice, because they renew themselves.

4. **Never set `GATEWAY_OAUTH_TOKEN_TTL=0`.** An immortal access token suppresses
   refresh-token issuance entirely, because there is nothing to refresh. You would
   get back exactly the situation this release fixed, plus a token that never
   expires.

### New setting

`GATEWAY_OAUTH_REFRESH_TTL`, seconds, default `7776000` (90 days). The default is
fine and you do not need to set it. `0` makes refresh tokens non-expiring, which is
not recommended.

### What to check afterwards

- Discovery lists both grants:

  ```bash
  curl -s https://<your-host>/.well-known/oauth-authorization-server
  ```

- The refresh branch is reachable. A bogus token should come back as
  `400 invalid_grant: unknown_refresh_token`. A `422` instead means the deploy did
  not take:

  ```bash
  curl -s -X POST https://<your-host>/oauth/token -d "grant_type=refresh_token&refresh_token=rft_bogus"
  ```

- Failures are being recorded. Needs an `admin`-scope token, which you mint with
  `python -m cortex_gateway.tokens_cli admin-key`:

  ```bash
  curl -s https://<your-host>/admin/auth-failures -H "Authorization: Bearer <admin-token>"
  ```

### One behavior worth knowing about

Presenting a refresh token that has already been rotated is treated as a leak, not
a mistake. The legitimate client would be holding the successor, so two parties
holding the same token means it escaped. The whole rotation family is revoked along
with every access token for that client, and the connector has to run the browser
consent flow again. The realistic way to trigger this by accident is restoring a
connector's token store from a backup or running two copies of a client against one
token store.

### Known gap this release does not close

`deploy.sh` creates the Container App environment without a log destination, so
your instance almost certainly retains **no server logs at all**; the only thing
available is the running replica's in-memory tail, which any restart erases. That
is why `auth_failures` exists: it is a durable trail for authentication problems
specifically. Everything else remains undiagnosable after a restart. If you want
real platform logs, create the environment with `--logs-destination log-analytics`
and a workspace, and accept the added cost.
