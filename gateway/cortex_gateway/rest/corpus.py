"""Corpus-backed REST endpoints - the read surface the app shares with the MCP
connectors (same engine), plus human journal + ingest write paths.

search / item / recent / narratives map 1:1 onto the MCP search / fetch /
cortex_recent tools. journal + ingest are app write paths. Portable `db` access.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from pydantic import BaseModel

from .. import corpus_service, corpus_writes, db
from ..auth import Principal, require_scope
from ..core_client import CoreWriteError, core

router = APIRouter(prefix="/v1", tags=["corpus"])

_app = require_scope("app")


@router.get("/search")
def search(q: str = Query(min_length=2),
           kinds: str = Query(default=""),
           days: int = Query(default=0, ge=0),
           limit: int = Query(default=40, le=200),
           principal: Principal = Depends(_app)):
    return corpus_service.search(principal, q, kinds=kinds, days=days, limit=limit)


@router.get("/item/{token}")
def item(token: str, principal: Principal = Depends(_app)):
    return corpus_service.fetch(principal, token)


@router.get("/recent")
def recent(days: int = Query(default=7, ge=1, le=90),
           limit: int = Query(default=40, le=200),
           principal: Principal = Depends(_app)):
    return corpus_service.recent(principal, days=days, limit=limit)


@router.get("/narratives")
def narratives(period: str = Query(default="weekly"),
               limit: int = Query(default=10, le=100),
               _: Principal = Depends(_app)):
    kind = {"daily": "daily", "weekly": "weekly",
            "monthly": "monthly", "yearly": "yearly"}.get(period, "weekly")
    if not db.has_table("temporal_narratives"):
        return {"period": kind, "narratives": []}
    rows = db.fetchall(
        "SELECT id, kind, period_label, period_start, period_end, narrative, "
        "created_at FROM temporal_narratives WHERE kind = :k "
        "ORDER BY period_start DESC", {"k": kind})
    return {"period": kind, "narratives": rows[:limit]}


class DayReprocessIn(BaseModel):
    day: str


@router.post("/day/reprocess")
def day_reprocess(body: DayReprocessIn, _: Principal = Depends(_app)):
    """Regenerate the daily narrative for one day, on demand (the
    phone's "reprocess this day" button). Forces a fresh row even when
    one exists, and the generation reads everything the day holds
    server-side (sessions, the notes digest, sleep parsing). Model
    choice rides the overseer's temporal-daily purpose, pinned cheap in
    plugin.toml and owner-tunable from the web Settings card. The long
    read timeout is the LLM call; a down core still fails in seconds
    on connect."""
    day = (body.day or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(400, "day must be YYYY-MM-DD")
    try:
        out = core().post(
            "/plugins/overseer/temporal/generate",
            {"kind": "daily", "period_label": day, "force": True},
            read=120.0)
    except CoreWriteError as e:
        raise HTTPException(502, f"could not regenerate the day: {e}")
    return out


# ── Human journal ─────────────────────────────────────────────────────


class JournalIn(BaseModel):
    text: str
    entry_type: str | None = "reflection"


@router.get("/journal")
def list_journal(limit: int = Query(default=30, le=200), _: Principal = Depends(_app)):
    if not db.has_table("human_journal_entries"):
        return {"entries": []}
    rows = db.fetchall(
        "SELECT * FROM human_journal_entries ORDER BY created_at DESC")
    return {"entries": rows[:limit]}


@router.post("/journal")
def create_journal(body: JournalIn, _: Principal = Depends(_app)):
    new_id = corpus_writes.insert_journal({
        "text": body.text, "entry_type": body.entry_type or "reflection"})
    row = db.fetchone("SELECT * FROM human_journal_entries WHERE id = :id",
                      {"id": new_id})
    return row or {"ok": True, "id": new_id}


# ── Ingest (intake pipeline) ──────────────────────────────────────────


class IngestIn(BaseModel):
    content: str
    kind: str | None = "note"
    tags: str | None = None
    project: str | None = None


@router.post("/ingest")
def ingest(body: IngestIn, principal: Principal = Depends(_app)):
    return corpus_service.ingest(
        principal, content=body.content, kind=body.kind or "note",
        tags=body.tags, project=body.project)
