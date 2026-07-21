"""Owner-facing connector access grant management (docs/CONNECTOR_GRANTS_DESIGN.md).

The phone app lists connections and approves / revokes them. Guarded by `app`
scope: the owner's trusted device. A connector token can NEVER hold `app`
(verified: OAuth/connector tokens are hard-capped to connector scopes), so a
connector can never manage its own or another connection's grant. Effects are
immediate - the corpus read gate consults the grant on every read.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import grants
from ..auth import Principal, require_scope

router = APIRouter(prefix="/v1/connections", tags=["connections"])

_app = require_scope("app")


class ApproveIn(BaseModel):
    level: str = "full"          # "none" | "full"
    always: bool = False         # also set approval_policy=always


class PolicyIn(BaseModel):
    approval_policy: str         # "ask" | "always"


@router.get("")
def list_connections(status: str = "", _: Principal = Depends(_app)):
    """All connections (or ?status=pending for the ones awaiting approval)."""
    return {"connections": grants.list_grants(status or None)}


@router.get("/{grant_id}")
def get_connection(grant_id: int, _: Principal = Depends(_app)):
    out = grants.get_by_id(grant_id)
    if out is None:
        raise HTTPException(404, "connection not found")
    return out


@router.post("/{grant_id}/approve")
def approve_connection(grant_id: int, body: ApproveIn, p: Principal = Depends(_app)):
    """Confirm a pending connection (or change an active one's level). Sets
    status=active + level; always=true also sets approval_policy=always."""
    try:
        out = grants.approve(grant_id, body.level, always=body.always, by=p.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if out is None:
        raise HTTPException(404, "connection not found")
    return out


@router.post("/{grant_id}/policy")
def set_connection_policy(grant_id: int, body: PolicyIn, _: Principal = Depends(_app)):
    try:
        out = grants.set_policy(grant_id, body.approval_policy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if out is None:
        raise HTTPException(404, "connection not found")
    return out


@router.post("/{grant_id}/revoke")
def revoke_connection(grant_id: int, _: Principal = Depends(_app)):
    """Disconnect: status=revoked, level=none, and revoke the connection's
    outstanding token(s) so it must re-OAuth to return."""
    out = grants.revoke(grant_id)
    if out is None:
        raise HTTPException(404, "connection not found")
    return {"ok": True, **out}
