"""Connector access grants - per-connection authorization.

One `connector_grants` row per OAuth `client_id`. Default deny: a connector
reads nothing until the owner approves it from the app. The corpus read gate
consults `has_full_access()`; the `/v1/connections` endpoints manage grants.
See docs/CONNECTOR_GRANTS_DESIGN.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from . import db
from .auth import Principal
from .config import get_settings

LEVELS = {"none", "full"}          # v1; "work"/"personal" are future
POLICIES = {"ask", "always"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)   # naive-UTC for storage


def _client_id(principal: Principal) -> str | None:
    """A connector's client_id: the token column, or parsed from an
    `oauth:<client_id>` name (covers tokens minted before the column existed)."""
    if principal.client_id:
        return principal.client_id
    name = principal.name or ""
    return name.split("oauth:", 1)[1] if name.startswith("oauth:") else None


def _redirect_host(client_id: str) -> str:
    row = db.fetchone("SELECT redirect_uris FROM oauth_clients WHERE client_id = :c",
                      {"c": client_id})
    for u in ((row["redirect_uris"] or "").splitlines() if row else []):
        h = urlparse(u).hostname
        if h:
            return h
    return ""


def grant_for(client_id: str | None) -> dict | None:
    if not client_id:
        return None
    return db.fetchone("SELECT * FROM connector_grants WHERE client_id = :c",
                       {"c": client_id})


def has_full_access(principal: Principal) -> bool:
    """A connector reads corpus content only if its connection is active AND
    full. App/phone (non-connector) tokens are unaffected."""
    if not principal.is_connector:
        return True
    g = grant_for(_client_id(principal))
    return bool(g and g["status"] == "active" and g["level"] == "full")


def can_write(principal: Principal) -> bool:
    """Write authorization. The owner (app/phone) always writes. A connector
    may write iff its connection is approved, the SAME grant that gates reads,
    or it carries an explicit connector:write scope (static tokens minted by
    the CLI). Tory 2026-07-22: an approved connection is read AND write;
    logging into Cortex is a first-class use, not a separate privilege, so
    approval alone is enough. Writes are additive, never destructive."""
    if principal.has("app") or principal.has("connector:write"):
        return True
    return principal.is_connector and has_full_access(principal)


def _update(client_id: str, updates: dict) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in updates)
    db.execute(f"UPDATE connector_grants SET {sets} WHERE client_id = :_cid",
               dict(updates, _cid=client_id))


# ── lifecycle ─────────────────────────────────────────────────────────

def upsert_on_connect(client_id: str, name: str, redirect_host: str = "") -> None:
    """Called when a connector's OAuth token is minted. Creates the grant on
    first sight (seeding hosts in GATEWAY_CONNECTOR_FULL_HOSTS to active/full/
    always, e.g. Grok); refreshes last_connected_at; and for an `ask` connector,
    resets an active grant to pending so the re-connection is re-confirmed."""
    if not client_id:
        return
    db.init_schema()
    host = (redirect_host or _redirect_host(client_id)).lower()
    if not name:
        r = db.fetchone("SELECT client_name FROM oauth_clients WHERE client_id = :c",
                        {"c": client_id})
        name = (r or {}).get("client_name") or client_id
    now = _now()
    g = grant_for(client_id)
    if g is None:
        seed = bool(host) and host in get_settings().connector_full_hosts
        db.insert("connector_grants", {
            "client_id": client_id, "name": name or client_id, "redirect_host": host,
            "level": "full" if seed else "none",
            "approval_policy": "always" if seed else "ask",
            "status": "active" if seed else "pending",
            "first_connected_at": now, "last_connected_at": now,
            "granted_at": now if seed else None,
            "granted_by": "seed" if seed else None, "updated_at": now,
        })
        return
    updates = {"last_connected_at": now, "updated_at": now,
               "name": name or g.get("name"),
               "redirect_host": host or g.get("redirect_host")}
    if g["approval_policy"] == "ask" and g["status"] == "active":
        updates["status"] = "pending"     # re-confirm each connection under 'ask'
    _update(client_id, updates)


def migrate() -> None:
    """Backfill a grant for every existing OAuth client that lacks one, so the
    read gate has a grant to consult immediately (grandfathers seed full-hosts
    like Grok without waiting for the next token mint). Run at startup."""
    db.init_schema()
    if not db.has_table("oauth_clients"):
        return
    for c in db.fetchall("SELECT client_id, client_name, redirect_uris FROM oauth_clients"):
        if grant_for(c["client_id"]) is None:
            host = ""
            for u in (c["redirect_uris"] or "").splitlines():
                h = urlparse(u).hostname
                if h:
                    host = h
                    break
            upsert_on_connect(c["client_id"], c.get("client_name") or "", host)


# ── management (backing the /v1/connections endpoints) ────────────────

def _shape(g: dict) -> dict:
    tok = db.fetchone(
        "SELECT last_used_at, revoked_at FROM gateway_tokens "
        "WHERE client_id = :c ORDER BY id DESC", {"c": g["client_id"]})
    return {
        "id": g["id"], "client_id": g["client_id"], "name": g.get("name"),
        "redirect_host": g.get("redirect_host"),
        "level": g["level"], "approval_policy": g["approval_policy"],
        "status": g["status"],
        "first_connected_at": str(g.get("first_connected_at") or "") or None,
        "last_connected_at": str(g.get("last_connected_at") or "") or None,
        "last_used_at": str((tok or {}).get("last_used_at") or "") or None,
        "granted_at": str(g.get("granted_at") or "") or None,
        "token_status": ("revoked" if tok and tok.get("revoked_at")
                         else "active" if tok else "none"),
    }


def list_grants(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM connector_grants"
    params: dict = {}
    if status:
        sql += " WHERE status = :s"
        params["s"] = status
    sql += " ORDER BY last_connected_at DESC"
    return [_shape(g) for g in db.fetchall(sql, params)]


def get_by_id(grant_id: int) -> dict | None:
    g = db.fetchone("SELECT * FROM connector_grants WHERE id = :i", {"i": grant_id})
    return _shape(g) if g else None


def approve(grant_id: int, level: str, always: bool = False,
            by: str | None = None) -> dict | None:
    if level not in LEVELS:
        raise ValueError(f"invalid level: {level} (valid: {sorted(LEVELS)})")
    g = db.fetchone("SELECT client_id FROM connector_grants WHERE id = :i", {"i": grant_id})
    if not g:
        return None
    updates = {"level": level, "status": "active", "granted_at": _now(),
               "granted_by": by, "updated_at": _now()}
    if always:
        updates["approval_policy"] = "always"
    _update(g["client_id"], updates)
    return get_by_id(grant_id)


def set_policy(grant_id: int, policy: str) -> dict | None:
    if policy not in POLICIES:
        raise ValueError(f"invalid approval_policy: {policy} (valid: {sorted(POLICIES)})")
    g = db.fetchone("SELECT client_id FROM connector_grants WHERE id = :i", {"i": grant_id})
    if not g:
        return None
    _update(g["client_id"], {"approval_policy": policy, "updated_at": _now()})
    return get_by_id(grant_id)


def revoke(grant_id: int) -> dict | None:
    """Disconnect: status=revoked, level=none, and revoke the connection's
    outstanding tokens so it must re-OAuth to return."""
    g = db.fetchone("SELECT client_id FROM connector_grants WHERE id = :i", {"i": grant_id})
    if not g:
        return None
    cid = g["client_id"]
    _update(cid, {"status": "revoked", "level": "none", "updated_at": _now()})
    n = db.execute_write(
        "UPDATE gateway_tokens SET revoked_at = CURRENT_TIMESTAMP "
        "WHERE client_id = :c AND revoked_at IS NULL", {"c": cid})
    out = get_by_id(grant_id)
    if out is not None:
        out["tokens_revoked"] = n
    return out
