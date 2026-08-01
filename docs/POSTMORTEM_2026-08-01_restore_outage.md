# Postmortem: the corpus was fine and the app still would not start

**2026-07-30 to 2026-08-01. Roughly two days down.** Zero data loss.

Read this if you operate a Cortex-Cloud instance. The bug is in `deploy/`, not in
anyone's Azure account, and the fix is already on master.

## What broke

Every authenticated surface was unreachable. The container app accepted TCP
connections and returned nothing, three separately-created revisions all wedged
at the same point, and the scheduled tick job failed on every run. Azure reported
the revision as `Healthy` throughout.

## What caused it

The restore init container ran `set -e` across all three databases in one loop:

```sh
set -e
for db in /data/cortex.db /data/plugins/overseer/overseer.db /data/gateway.db; do
  litestream restore -if-db-not-exists -if-replica-exists "$db"
done
```

`gateway.db`'s Litestream replica developed a broken generation:

```
cannot find max wal index for restore: missing initial wal segment:
generation=e3a1df83c119fc9d index=00000003 offset=4152
```

litestream exited 1. `set -e` killed the init container. Init containers run
before everything, so the four main containers never started.

The corpus was healthy the entire time. `cortex.db` and `overseer.db` restored
perfectly on every single attempt, WAL fully applied. **A damaged replica of the
least valuable database took the whole product down.**

## Why it took two days to find

Four things conspired, and each one is worth internalising.

**1. The symptom pointed at the wrong layer.** With the init container dead, the
platform surfaced only:

```
NotRunning - "System Identity Container is still running."
```

That names Azure's own identity sidecar. It reads like a platform fault. It is
not: it is what ACA says when a replica never gets past init.

**2. `healthState` lied.** The revision reported `Healthy` at 100% traffic while
every request failed. ACA's injected default probe targets the ingress port,
where the auth sidecar answers, so it never observed that the app behind it was
absent.

**3. Rebuilding did not help, which looked like more evidence for a platform
fault.** A revision restart, a revision copy, a fresh image, a brand-new app and
a brand-new managed environment all failed identically, because every one of them
pulled the same broken blob.

**4. There were no logs.** `appLogsConfiguration.destination` was null on the
environment, so nothing was retained beyond the running replica's in-memory tail.

## What actually broke it open

Two moves, in order:

**A minimal control app** (`mcr.microsoft.com/k8se/quickstart`, no identity, no
secrets, no init container) deployed into the same new environment served `200`
in 0.13 seconds. That proved the platform, environment and region were all
healthy and the fault was ours. Adding system-assigned identity to a second
control app also served `200`, which cleared identity too.

**A Log Analytics workspace** attached to the new environment. It showed the real
error on the first query. That logging gap had been flagged twice in previous
sessions and never closed; it paid for itself within the hour.

## The fix

`deploy/litestream-restore.sh` now distinguishes databases by whether they can be
rebuilt:

| Database | Policy | Reason |
|---|---|---|
| `cortex.db` | **abort** | Irreplaceable. Booting empty would let the replicate sidecar overwrite the good replica and destroy the corpus. |
| `overseer.db` | **abort** | Costly to regenerate: vectors, prompt library, processing bookkeeping. |
| `gateway.db` | **warn, clear partials, continue** | Tokens, OAuth clients, grants, sync cursors. Rebuildable by reconnecting clients. Never worth an outage. |

The rule: **fail hard only for data that cannot be rebuilt.**

Note the asymmetry is deliberate and not merely "be lenient". Continuing past a
failed `cortex.db` restore would be actively destructive, because the replicate
sidecar would then push an empty database over the good replica. That is why
those two still abort.

## Recovery steps taken

1. Downloaded and **verified** the full backup before touching anything:
   38 MB Litestream, 1.2 GB raw imports. Snapshots decompressed and row-counted,
   not merely downloaded.
2. Deleted only the corrupt `gateway.db` replica from Blob. Corpus replicas
   untouched.
3. Built a replacement app in a new environment **with a log destination wired**.
4. Confirmed the corpus restored: 3,942 gists, 2,214 notes, 3,883 imported
   sessions, 200 projects, 289 narratives, 195 people.
5. Moved the custom domain, issued a managed certificate, reattached Entra auth
   using the same app registration.
6. Synced the tick job's token to the rebuilt app's service token.

## Consequences

`gateway.db` was lost. That means tokens, OAuth clients and connector grants are
gone, so **every connected AI client must reconnect once**. The corpus, the
imports and all interpretive layers are intact.

## Follow-ups worth doing

- **Wire a log destination on every environment.** `deploy.sh` creates the
  managed environment without one. Every friend deploy currently has this same
  blind spot. This is the single highest-value change in the list.
- **Make the health probe observe the app**, not the ingress port, so a dead
  gateway cannot report `Healthy`.
- **Alert on repeated tick-job failure.** The job started failing at
  2026-07-30 15:00 UTC and nobody was told. That was the earliest available
  signal, roughly a day before the outage was noticed.
- **Scope CI role assignments to survive a rebuild.** The deploy principal is
  granted `Contributor` per-resource, so a rebuilt app is invisible to CI until
  re-granted. Kept narrow deliberately, since the resource group also holds the
  Key Vault and the corpus storage account, but the tradeoff should be a
  conscious one.
