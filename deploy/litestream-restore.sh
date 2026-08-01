#!/bin/sh
# INIT container: restore the corpus from Blob BEFORE core/gateway start.
# Ordering is the whole point: if the core booted first it would create
# EMPTY DBs, restore would skip (-if-db-not-exists), and the replicate
# sidecar would then overwrite the good replica with the empty DB.
# Running this as an ACA init container makes that impossible.
#
# WHY THE PER-DB SPLIT BELOW (2026-08-01 outage, ~2 days of downtime):
# this used to be `set -e` over all three databases in one loop. The
# gateway.db replica developed a broken generation ("cannot find max wal index
# for restore: missing initial wal segment"), litestream exited 1, the init
# container died, and the four main containers therefore NEVER STARTED. The
# corpus was healthy the whole time: cortex.db and overseer.db restored
# perfectly on every attempt. A damaged replica of the LEAST valuable database
# took the entire product down. Worse, because init containers fail before
# anything can serve, the platform surfaced only "System Identity Container is
# still running", which reads like an Azure fault rather than our restore step.
#
# The rule this encodes: fail hard ONLY for data that cannot be rebuilt.
#   cortex.db   the owner's corpus. Irreplaceable. A failed restore MUST abort,
#               because booting would create an empty DB and the replicate
#               sidecar would then overwrite the good replica with it.
#   overseer.db AI process state (vectors, prompts, bookkeeping). Expensive to
#               regenerate, so abort as well.
#   gateway.db  tokens, OAuth clients, grants, sync cursors. REBUILDABLE by
#               reconnecting clients. Never worth an outage: warn, continue,
#               and let the app recreate it.
set -u
mkdir -p /data/plugins/overseer

restore_or_die() {
  db="$1"
  if litestream restore -if-db-not-exists -if-replica-exists "$db"; then
    echo "restore ok (required): $db"
  else
    echo "FATAL: restore failed for $db, which cannot be rebuilt." >&2
    echo "Refusing to boot: an empty DB here would be replicated over the" >&2
    echo "good replica and destroy the corpus. Repair the replica first." >&2
    exit 1
  fi
}

restore_or_warn() {
  db="$1"
  if litestream restore -if-db-not-exists -if-replica-exists "$db"; then
    echo "restore ok (optional): $db"
  else
    echo "WARNING: restore failed for $db. It is rebuildable, so startup" >&2
    echo "continues and the app will recreate it. Expect connected clients to" >&2
    echo "need re-authorization. Investigate the replica when convenient." >&2
    # Clear any partial artifact so the app starts from a clean file.
    rm -f "$db" "$db-wal" "$db-shm" "$db.tmp" 2>/dev/null || true
  fi
}

restore_or_die  /data/cortex.db
restore_or_die  /data/plugins/overseer/overseer.db
restore_or_warn /data/gateway.db

echo "litestream restore phase complete"
