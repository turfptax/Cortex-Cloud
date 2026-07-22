"""Bearer-token auth - portable (SQLAlchemy) over the canonical database.

Tokens are stored hashed in `gateway_tokens` with:
  kind             'app' | 'connector' | 'oauth' | 'admin'
  scopes           app | connector:read | connector:write | admin
  max_tier         sensitivity ceiling (public|internal|confidential|restricted)
  category_filter  optional CSV of categories the token may see
  key_prefix       non-secret prefix shown in listings
  expires_at       optional; NULL = never (connector keys are long-lived)

`auth.py` covers verification + low-level mint. The connector-key *management*
surface (create/list/revoke/rotate) lives in `connectors.py`; the phone OAuth
flow lives in `oauth.py`. All three write the same table, so verification is one
code path.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

# Stored scope/category lists are comma-delimited, but the OAuth layer emits
# space-delimited (RFC 6749). Split on either so a delimiter mismatch upstream
# can never silently produce one malformed scope element.
_LIST_SPLIT = re.compile(r"[,\s]+")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing 'Z' on all runtimes
    (fromisoformat only learned 'Z' in 3.11)."""
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))

from fastapi import Depends, Header, HTTPException, status

from . import db


def ensure_schema() -> None:
    db.init_schema()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _coerce_expiry(expires_at: str | datetime | None) -> datetime | None:
    """Normalize an expiry to a NAIVE UTC datetime for storage. The SQLite
    dialect rejects strings on DateTime columns, and pyodbc can balk at
    tz-aware datetimes on a plain DATETIME column, so naive-UTC is the one
    shape portable across both. `_lookup` reads it back as UTC."""
    if expires_at is None:
        return None
    dt = expires_at if isinstance(expires_at, datetime) else _parse_iso(str(expires_at))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def mint(name: str, scopes: str, max_tier: str = "internal",
         category_filter: str | None = None, *, kind: str = "app",
         note: str | None = None,
         expires_at: str | datetime | None = None,
         client_id: str | None = None) -> str:
    """Create a token row, store its hash, return the raw token ONCE."""
    ensure_schema()
    raw = "ctx_" + secrets.token_urlsafe(32)
    db.insert("gateway_tokens", {
        "name": name, "kind": kind, "token_hash": hash_token(raw),
        "key_prefix": raw[:12], "scopes": scopes, "max_tier": max_tier,
        "category_filter": category_filter, "note": note,
        "expires_at": _coerce_expiry(expires_at), "client_id": client_id,
    })
    return raw


@dataclass
class Principal:
    id: int
    name: str
    kind: str
    scopes: set[str]
    max_tier: str
    category_filter: list[str]
    client_id: str | None = None

    def has(self, scope: str) -> bool:
        if scope in self.scopes:
            return True
        if scope == "connector:read" and "connector:write" in self.scopes:
            return True
        # The owner-device `hub` scope implies `app` (full REST + connection
        # management). It NEVER implies connector scopes, and connector/oauth
        # tokens never implies `hub` (hub is minted only for the hub client).
        if scope == "app" and "hub" in self.scopes:
            return True
        return False

    @property
    def is_connector(self) -> bool:
        return any(s.startswith("connector") for s in self.scopes)


def _lookup(raw: str) -> Principal | None:
    ensure_schema()
    row = db.fetchone(
        "SELECT * FROM gateway_tokens WHERE token_hash = :h AND revoked_at IS NULL",
        {"h": hash_token(raw)})
    if not row:
        return None
    # Honour optional expiry (connector keys default to never).
    exp = row.get("expires_at")
    if exp:
        try:
            exp_dt = exp if isinstance(exp, datetime) else _parse_iso(str(exp))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        except Exception:
            # Fail CLOSED: an expiry we can't parse must not read as "never
            # expires" on a security-critical auth gate.
            return None
        if exp_dt < datetime.now(timezone.utc):
            return None
    db.execute("UPDATE gateway_tokens SET last_used_at = CURRENT_TIMESTAMP "
               "WHERE id = :id", {"id": row["id"]})
    cats = [c for c in _LIST_SPLIT.split((row.get("category_filter") or "").strip()) if c]
    return Principal(
        id=row["id"], name=row["name"], kind=row.get("kind") or "connector",
        scopes={s for s in _LIST_SPLIT.split((row["scopes"] or "").strip()) if s},
        max_tier=row.get("max_tier") or "internal", category_filter=cats,
        client_id=row.get("client_id"))


def principal_from_bearer(authorization: str | None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    principal = _lookup(authorization.split(" ", 1)[1].strip())
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "invalid or revoked token",
                            headers={"WWW-Authenticate": "Bearer"})
    return principal


# ── FastAPI dependencies ──────────────────────────────────────────────


async def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    return principal_from_bearer(authorization)


def require_scope(scope: str):
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"token lacks required scope: {scope}")
        return principal
    return _dep
