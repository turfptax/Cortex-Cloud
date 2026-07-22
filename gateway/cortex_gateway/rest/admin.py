"""Admin REST - connector key management without shell access.

Guarded by the `admin` scope (mint an admin key with the CLI:
`python -m cortex_gateway.tokens_cli admin-key`). Lets Tory create/list/revoke/
rotate connector keys from a tool or the Hub. Raw keys are returned ONCE.

This is the same capability as the `connector` CLI subcommands - exposed over
HTTP for convenience. It is SEPARATE from the phone OAuth flow.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel

from .. import connectors, monitor
from ..auth import Principal, require_scope

router = APIRouter(prefix="/admin", tags=["admin"])

_admin = require_scope("admin")


class ConnectorIn(BaseModel):
    name: str
    scope: str = "connector:read"          # connector:read | connector:write
    max_tier: str = "internal"             # public|internal|confidential|restricted
    categories: str | None = None
    note: str | None = None
    expires_at: str | None = None          # ISO; omit for a long-lived key


@router.get("/connectors")
def list_connectors(_: Principal = Depends(_admin)):
    return {"connectors": connectors.list_keys()}


@router.post("/connectors", status_code=201)
def create_connector(body: ConnectorIn, _: Principal = Depends(_admin)):
    try:
        raw = connectors.create(
            body.name, scope=body.scope, max_tier=body.max_tier,
            categories=body.categories, note=body.note, expires_at=body.expires_at)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "ok": True,
        "key": raw,  # shown once
        "warning": "Copy this key now - it is not retrievable later.",
        "name": body.name, "scope": body.scope, "max_tier": body.max_tier,
    }


@router.post("/connectors/{key_id}/revoke")
def revoke_connector(key_id: int, _: Principal = Depends(_admin)):
    if not connectors.revoke(key_id):
        raise HTTPException(404, f"connector key not found: {key_id}")
    return {"ok": True, "revoked": key_id}


@router.post("/connectors/{key_id}/rotate")
def rotate_connector(key_id: int, _: Principal = Depends(_admin)):
    raw = connectors.rotate(key_id)
    if raw is None:
        raise HTTPException(404, f"connector key not found: {key_id}")
    return {"ok": True, "rotated_from": key_id, "key": raw,
            "warning": "Copy this key now - it is not retrievable later."}


@router.get("/monitor")
def read_monitor(hours: float = 1.0, _: Principal = Depends(_admin)):
    """Exfiltration check over the corpus read log: anomaly alerts plus a
    per-caller activity summary for the trailing `hours` window."""
    return monitor.analyze(window_hours=hours)
