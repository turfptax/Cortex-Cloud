"""OAuth refresh tokens: issue, rotate, revoke (RFC 6749 section 6 + OAuth 2.1).

Why this exists: access tokens carry a finite TTL, and before refresh tokens the
only way to get a new one was to re-run the whole browser consent flow. A
connector cannot do that on its own (it needs a human at a browser), so every
expiry surfaced as a dead connection the owner had to reconnect by hand.

Lives in its own module rather than inside oauth.py because the revoke paths in
grants.py and connectors.py must reach it, and oauth.py already imports grants;
putting it here keeps the import graph acyclic.

Rotation with reuse detection is the security model:
  - redeeming a refresh token marks it used and mints a successor in the same
    family, so a captured-but-unused token is only good until the real client
    next refreshes;
  - presenting an ALREADY-USED token means two parties hold it, i.e. it leaked,
    because the legitimate client would be holding the successor. The entire
    family is revoked, along with every access token for that client.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from . import auth, db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema() -> None:
    db.init_schema()


def issue(*, client_id: str, name: str, kind: str, scope: str, max_tier: str,
          ttl: int, family_id: str | None = None) -> str:
    """Mint a refresh token; return the raw value ONCE (only its hash is kept).

    `family_id` continues an existing rotation chain; omitted starts a new one.
    """
    ensure_schema()
    raw = "rft_" + secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=ttl) if ttl > 0 else None
    db.insert("oauth_refresh_tokens", {
        "token_hash": auth.hash_token(raw),
        "family_id": family_id or ("fam_" + secrets.token_urlsafe(12)),
        "client_id": client_id, "name": name, "kind": kind,
        "scope": scope, "max_tier": max_tier, "expires_at": expires_at,
    })
    return raw


def revoke_family(family_id: str) -> int:
    """Revoke every token in a rotation chain. Returns rows affected."""
    if not db.has_table("oauth_refresh_tokens"):
        return 0
    return db.execute_write(
        "UPDATE oauth_refresh_tokens SET revoked_at = CURRENT_TIMESTAMP "
        "WHERE family_id = :f AND revoked_at IS NULL", {"f": family_id})


def revoke_for_client(client_id: str) -> int:
    """Revoke every refresh token belonging to a connection.

    Called wherever a connection's access tokens are revoked (grant revoke,
    startup dedupe, single-key revoke). Without this a revoked connector could
    mint itself a fresh access token and come straight back to life.
    """
    if not client_id or not db.has_table("oauth_refresh_tokens"):
        return 0
    return db.execute_write(
        "UPDATE oauth_refresh_tokens SET revoked_at = CURRENT_TIMESTAMP "
        "WHERE client_id = :c AND revoked_at IS NULL", {"c": client_id})


class RefreshError(Exception):
    """Refresh rejected. `reason` is a short, non-secret audit tag."""

    def __init__(self, reason: str, *, breach: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.breach = breach


def redeem(raw: str, *, client_id: str = "") -> dict:
    """Validate and consume a refresh token, returning its stored row.

    Raises RefreshError on every rejection path. On detected reuse the family is
    revoked before raising, with breach=True so the caller can audit it loudly.
    """
    ensure_schema()
    row = db.fetchone(
        "SELECT * FROM oauth_refresh_tokens WHERE token_hash = :h",
        {"h": auth.hash_token(raw)})
    if not row:
        raise RefreshError("unknown_refresh_token")
    if row.get("revoked_at"):
        raise RefreshError("refresh_token_revoked")
    if row.get("used_at"):
        # Already rotated. The legitimate client holds the successor, so
        # whoever presented this one should not have it: burn the chain.
        revoke_family(row["family_id"])
        auth.revoke_for_client(row["client_id"])
        raise RefreshError("refresh_token_reuse", breach=True)
    try:
        exp_dt = auth.read_expiry(row.get("expires_at"))
    except Exception:
        # Fail CLOSED, matching the access-token gate: an expiry we cannot
        # parse must never read as "never expires".
        raise RefreshError("refresh_token_bad_expiry") from None
    if exp_dt is not None and exp_dt < _now():
        raise RefreshError("refresh_token_expired")
    # A client_id is optional on a public-client refresh, but if one is sent it
    # must match: a token is only usable by the client it was issued to.
    if client_id and row["client_id"] != client_id:
        raise RefreshError("refresh_client_mismatch")
    # Defense in depth: an owner-revoked connection must not refresh back to
    # life even if a revoke path someday forgets to call revoke_for_client.
    if db.has_table("connector_grants"):
        g = db.fetchone("SELECT status FROM connector_grants "
                        "WHERE client_id = :c", {"c": row["client_id"]})
        if g and g.get("status") == "revoked":
            revoke_family(row["family_id"])
            raise RefreshError("connection_revoked")
    # Atomic single-use: only the caller that flips used_at NULL -> now may
    # proceed, so two concurrent refreshes with one token cannot both mint.
    if db.execute_write(
            "UPDATE oauth_refresh_tokens SET used_at = CURRENT_TIMESTAMP "
            "WHERE token_hash = :h AND used_at IS NULL",
            {"h": auth.hash_token(raw)}) != 1:
        raise RefreshError("refresh_token_race")
    return row
