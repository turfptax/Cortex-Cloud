# Cortex Cloud

The cloud layer of [Cortex](https://github.com/turfptax/cortex-core), a
personal AI memory system you own end to end. Cortex Cloud is a single
Azure Container App that turns the Cortex engine into a private website:
sign in with your Microsoft account and your entire memory corpus is a
URL, reachable from any browser or phone, with AI connectors (Claude,
ChatGPT, Grok) able to read it over MCP.

This repo is **both** the gateway service and the deploy kit. Clone it,
point it at your own Azure subscription, and stand up your own instance
in about 15 minutes. Nothing is shared between instances: your corpus,
your keys, your tenant, your bill (~$7-9/month at idle).

> Single-owner by design. The web Hub is locked to exactly one Microsoft
> account: yours. Others in your tenant can't even sign in.

## What runs

One Container App, four containers off a single image, sharing an
ephemeral volume; scale-to-zero so idle ≈ free:

| Container | Role |
|-----------|------|
| **core** | the memory engine + interpretive loop; sole writer of the SQLite corpus |
| **gateway** | public ingress (port 8430): the `/api` façade, the web Hub SPA, the OAuth 2.1 server, and the MCP endpoint |
| **embed** | a small llama.cpp server for semantic recall |
| **litestream** | continuously replicates the SQLite DBs to Blob storage |
| *init:* **restore** | restores the DBs from Blob on cold start (restore-before-writer = the data-safety rule) |

The corpus lives in SQLite files, replicated to Blob by Litestream. There
is no database server to run or pay for; each person gets a fully isolated
instance rather than a shared multi-tenant database.

```
                 you (browser / phone)         AI connectors
                        │  Entra login            │  OAuth 2.1 + MCP
                        ▼                          ▼
        ┌──────────────────────────────────────────────────┐
        │  gateway  :8430  (public)                         │
        │   /  SPA   /api façade   /oauth   /mcp   /ops/tick │
        └───────────────┬──────────────────────────────────┘
                        │ localhost (service token)
        ┌───────────────▼───────────┐   ┌──────────────┐
        │  core :8420 (private)      │──▶│ embed :8082  │
        │  SQLite corpus + loop      │   └──────────────┘
        └───────────────┬───────────┘
                        │ litestream
                   ┌────▼────┐
                   │  Blob   │  (restore on cold start)
                   └─────────┘
```

## Prerequisites

- An **Azure subscription** (`az login`), with the Azure CLI installed.
- A **Microsoft account** in your own Entra tenant (that's the single owner).
- An **OpenRouter API key** ([openrouter.ai/keys](https://openrouter.ai/keys)) to fund the memory loop.
- `git`, `node`/`npm` (to build the web UI), and `gettext` (`envsubst`), if you build the image yourself.
- *(Optional)* a domain you control, for a custom URL.

## Deploy

```bash
git clone https://github.com/turfptax/Cortex-Cloud.git
cd Cortex-Cloud
cp .env.example .env
# edit .env - subscription, a unique NAME_SUFFIX, your OWNER_OID, your OpenRouter key
#   OWNER_OID:  az ad signed-in-user show --query id -o tsv

bash deploy/deploy.sh          # provisions everything, prints your URL
bash deploy/tick-job.sh        # schedules the memory loop (a few wake-ups/day)
```

`deploy.sh` creates the resource group, container registry, storage, Key
Vault, the Entra app (locked to you), builds the image from source, and
deploys the Container App with Easy Auth. Open the printed URL, sign in
with your Microsoft account, and you have an empty corpus ready to fill.

> **Read before you run.** These scripts codify the exact sequence used to
> stand up the reference instance, but they have not been CI-tested against
> a fresh subscription. Run once in a throwaway resource group, and read
> the output at each of the nine steps. `az` behavior varies by CLI version
> and OS (some subcommands are quirky on Windows - WSL or Cloud Shell is
> smoothest).

### Using a pre-built image instead of building

If someone shares a published image with you, set `IMAGE` in `.env` (e.g.
`IMAGE=ghcr.io/<owner>/cortex-cloud:latest`) and `deploy.sh` skips the
build step. Otherwise it builds from source into your own registry - see
[`deploy/build-image.sh`](deploy/build-image.sh), which clones
`cortex-core` and `cortex-desktop`, builds the SPA, and runs `az acr build`.

## Security model

- **Sign-in is locked to one account.** The Entra app registration is
  single-tenant with `appRoleAssignmentRequired=true` and only you
  assigned, so Entra refuses to issue a session to anyone else, before
  any app code runs.
- **Owner pin in the app.** Every `/api` call is additionally checked
  against your Entra object id (`GATEWAY_OWNER_OIDS`); a stray session
  gets 403, never the corpus.
- **Secrets stay out of code.** The LLM key lives in Key Vault; the
  core↔gateway service token and the storage key are Container App
  secrets. Nothing sensitive is in this repo (see `.gitignore`), and the
  image build refuses to stage any `.db`/identity file.
- **Connectors are read-only and default-deny.** AI connectors authorize
  via OAuth 2.1 + PKCE and start with no access until you grant it.

## The gateway service

The `cortex_gateway/` package is a FastAPI app: an OAuth 2.1 authorization
server, the `/api` façade the web Hub calls (forwarding to the co-located
core with the service token injected server-side, so the browser never
holds a corpus credential), a Streamable-HTTP MCP endpoint for AI
connectors, and static serving of the built Hub SPA. Tests: `pytest`.

See [`DESIGN.md`](DESIGN.md) and [`docs/`](docs/) for the OAuth flow, the
connector-grant model, and the Entra/Easy-Auth setup.

## Related repos

- [cortex-core](https://github.com/turfptax/cortex-core) - the memory engine (the `core` container).
- [cortex-desktop](https://github.com/turfptax/cortex-desktop) - the local Hub + the web UI source (the SPA in the `gateway` container).

## License

MIT - see [LICENSE](LICENSE).
