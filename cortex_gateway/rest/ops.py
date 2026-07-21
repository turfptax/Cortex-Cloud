"""Operational endpoints for the co-located cloud topology.

POST /ops/tick - wake-and-tick: proxies to the core's
                  /plugins/overseer/tick-now over localhost. This is
                  the scale-to-zero heartbeat: an ACA cron Job calls it
                  through the PUBLIC ingress (the only thing that wakes
                  a zero-scaled app), the replica spins up (init
                  restore -> core -> gateway), and the tick runs.

Auth: constant-time compare against CORTEX_SERVICE_TOKEN (the same
secret the gateway already holds to talk to the core). Deliberately
NOT a gateway_tokens row: the tick job must work on a fresh deployment
before any token has ever been minted in gateway.db.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request

from ..config import get_settings
from ..core_client import CoreWriteError, core

router = APIRouter(prefix="/ops", tags=["ops"])


def _check_service_token(request: Request) -> None:
    authz = request.headers.get("authorization") or ""
    token = authz.split(" ", 1)[1].strip() if " " in authz else ""
    expected = get_settings().core_token
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "invalid ops token")


@router.post("/tick")
def tick(request: Request):
    _check_service_token(request)
    try:
        out = core().post("/plugins/overseer/tick-now", {})
    except CoreWriteError as e:
        raise HTTPException(502, f"core tick failed: {e}")
    return {"ok": True, "core": out}
