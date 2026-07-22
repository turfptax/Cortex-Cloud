"""Easy Auth principal decoding + owner authorization.

Azure Container Apps built-in auth (Easy Auth) injects a base64-JSON
`x-ms-client-principal` header for logged-in users and STRIPS it from
inbound requests, so its presence proves an authenticated session. But
presence is not authorization: a single-tenant Entra app with
appRoleAssignmentRequired=false admits ANY account in the tenant. For a
single-owner corpus, the web UI must pin access to the owner's object
id, not merely "is signed in" (review finding, 2026-07-21).

Shared by app.py (middleware) and rest/hub_api.py (the /api facade);
kept in its own module to avoid a circular import (app.py builds the
facade router lazily inside create_app()).
"""
from __future__ import annotations

import base64
import json

# Entra object-id claim can arrive under either the short or the
# SOAP-schema claim type depending on the token version.
_OID_CLAIMS = (
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
    "oid",
)


def decode_principal(header_val: str) -> dict:
    """Decode the base64-JSON principal to {auth_typ, claims:{typ:val}}.
    Returns {} on any malformation (never raises)."""
    try:
        data = json.loads(base64.b64decode(header_val))
    except Exception:
        return {}
    claims = {c.get("typ"): c.get("val")
              for c in data.get("claims", []) if isinstance(c, dict)}
    return {"auth_typ": data.get("auth_typ"), "claims": claims}


def principal_oid(header_val: str) -> str | None:
    """The signed-in account's Entra object id (lowercased), or None."""
    claims = decode_principal(header_val).get("claims", {})
    for key in _OID_CLAIMS:
        val = claims.get(key)
        if val:
            return str(val).lower()
    return None


def owner_ok(header_val: str, owner_oids: frozenset[str]) -> bool:
    """True if this principal is an approved owner.

    owner_oids EMPTY = no allowlist configured: fall back to
    presence-only (any authenticated tenant account passes). That is
    the pre-pin behavior, kept so a fresh friend-deploy works before
    the owner is configured; startup logs a warning when web UI is on
    but no owner is pinned. Set GATEWAY_OWNER_OIDS to actually enforce.
    """
    if not header_val:
        return False
    if not owner_oids:
        return True
    oid = principal_oid(header_val)
    return bool(oid and oid in owner_oids)
