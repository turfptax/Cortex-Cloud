"""Durable record of authentication failures.

The Container App environment has no log destination configured, so the only
server-side history was the running replica's in-memory tail: a restart erased
it. Every past "the connector broke again" was therefore undiagnosable after the
fact. These rows live in gateway.db, which is replicated, so the next failure
leaves evidence.

Deliberately cheap and non-fatal: a logging problem must never turn a 401 into a
500, so every entry point swallows its own exceptions.
"""
from __future__ import annotations

import logging

from . import db

log = logging.getLogger("cortex_gateway.authlog")

_RETENTION_DAYS = 90
_pruned = False
_schema_ready = False


def _ensure_schema_once() -> None:
    """create_all() on every 401 would be a wasted round trip per request, and
    app startup already ran it. Ensure once per process instead."""
    global _schema_ready
    if _schema_ready:
        return
    db.init_schema()
    _schema_ready = True


def _prune_once() -> None:
    """Trim old rows one time per process. The table is tiny (a failure is rare
    and each row is a few hundred bytes), so this is housekeeping, not a hot
    path: no need to prune on every write."""
    global _pruned
    if _pruned:
        return
    _pruned = True
    try:
        db.execute(
            "DELETE FROM auth_failures WHERE occurred_at < "
            f"datetime('now', '-{_RETENTION_DAYS} days')"
            if db.is_sqlite() else
            "DELETE FROM auth_failures WHERE occurred_at < "
            f"DATEADD(day, -{_RETENTION_DAYS}, GETUTCDATE())")
    except Exception as e:  # noqa: BLE001 - housekeeping must never break auth
        log.debug("auth_failures prune skipped: %s", e)


def _prefix(authorization: str | None) -> str | None:
    """First 12 chars of the presented bearer, matching the existing non-secret
    gateway_tokens.key_prefix convention. Enough to correlate a failure with a
    known token; far too little to reconstruct one."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()[:12] or None


def record(reason: str, *, path: str = "", authorization: str | None = None,
           client_id: str | None = None, source_ip: str | None = None,
           user_agent: str | None = None) -> None:
    """Write one failure row. Never raises."""
    try:
        _ensure_schema_once()
        _prune_once()
        db.insert("auth_failures", {
            "reason": (reason or "")[:80],
            "path": (path or "")[:200] or None,
            "client_id": (client_id or None),
            "key_prefix": _prefix(authorization),
            "source_ip": (source_ip or None),
            "user_agent": (user_agent or "")[:300] or None,
        })
    except Exception as e:  # noqa: BLE001 - a 401 must stay a 401
        log.debug("auth_failures insert skipped: %s", e)


def recent(limit: int = 50) -> list[dict]:
    """Most recent failures, newest first. Backs operator inspection."""
    try:
        return db.fetchall(
            "SELECT id, reason, path, client_id, key_prefix, source_ip, "
            "user_agent, occurred_at FROM auth_failures "
            "ORDER BY id DESC LIMIT :n", {"n": int(limit)})
    except Exception as e:  # noqa: BLE001
        log.debug("auth_failures read failed: %s", e)
        return []
