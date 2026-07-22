"""Connector key management - long-lived API keys for external AI connectors
(Grok, ChatGPT, Claude, …) that authenticate via `Authorization: Bearer <key>`.

This is deliberately SEPARATE from the phone app's OAuth flow (`oauth.py`):
connectors that can't do an interactive OAuth dance (Grok, scripts, server-side
agents) use a key minted here. Both ultimately write `gateway_tokens`, so
verification stays one code path (`auth._lookup`).

Capabilities: create · list · revoke · rotate, each scoped (connector:read /
connector:write) with a per-connector sensitivity ceiling (`max_tier`) and an
optional category filter. Keys are long-lived (no expiry by default) but may be
given an `expires_at`. The raw key is shown ONCE at creation; only its hash is
stored. `key_prefix` (first 12 chars, non-secret) identifies a key in listings.
"""
from __future__ import annotations

from . import auth, db

VALID_SCOPES = {"connector:read", "connector:write"}
VALID_TIERS = {"public", "internal", "confidential", "restricted"}


def create(name: str, *, scope: str = "connector:read", max_tier: str = "internal",
           categories: str | None = None, note: str | None = None,
           expires_at: str | None = None) -> str:
    """Create a connector key. Returns the raw key once."""
    scopes = {s.strip() for s in scope.split(",") if s.strip()}
    bad = scopes - VALID_SCOPES
    if bad:
        raise ValueError(f"invalid connector scope(s): {sorted(bad)}; "
                         f"valid: {sorted(VALID_SCOPES)}")
    if max_tier not in VALID_TIERS:
        raise ValueError(f"invalid max_tier '{max_tier}'; valid: {sorted(VALID_TIERS)}")
    return auth.mint(name, ",".join(sorted(scopes)), max_tier, categories,
                     kind="connector", note=note, expires_at=expires_at)


def list_keys(*, include_revoked: bool = True) -> list[dict]:
    sql = "SELECT * FROM gateway_tokens WHERE kind = 'connector'"
    if not include_revoked:
        sql += " AND revoked_at IS NULL"
    sql += " ORDER BY id"
    out = []
    for r in db.fetchall(sql):
        out.append({
            "id": r["id"], "name": r["name"], "key_prefix": r.get("key_prefix"),
            "scopes": r["scopes"], "max_tier": r["max_tier"],
            "categories": r.get("category_filter") or "",
            "note": r.get("note") or "",
            "created_at": str(r.get("created_at") or ""),
            "last_used_at": str(r.get("last_used_at") or "") or None,
            "expires_at": str(r.get("expires_at") or "") or None,
            "status": "revoked" if r.get("revoked_at") else "active",
        })
    return out


def get(key_id: int) -> dict | None:
    keys = [k for k in list_keys() if k["id"] == key_id]
    return keys[0] if keys else None


def revoke(key_id: int) -> bool:
    row = db.fetchone("SELECT id FROM gateway_tokens WHERE id = :id "
                      "AND kind = 'connector'", {"id": key_id})
    if not row:
        return False
    db.execute("UPDATE gateway_tokens SET revoked_at = CURRENT_TIMESTAMP "
               "WHERE id = :id", {"id": key_id})
    return True


def rotate(key_id: int) -> str | None:
    """Revoke the old key and issue a new one carrying the same name/scope/tier/
    categories. Returns the new raw key, or None if the id isn't a connector key."""
    row = db.fetchone("SELECT * FROM gateway_tokens WHERE id = :id "
                      "AND kind = 'connector'", {"id": key_id})
    if not row:
        return None
    revoke(key_id)
    return auth.mint(row["name"], row["scopes"], row.get("max_tier") or "internal",
                     row.get("category_filter"), kind="connector",
                     note=(row.get("note") or "") + " (rotated)")
