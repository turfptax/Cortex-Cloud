"""Corpus-backed REST endpoints - the read surface the app shares with the MCP
connectors (same engine), plus human journal + ingest write paths.

search / item / recent / narratives map 1:1 onto the MCP search / fetch /
cortex_recent tools. journal + ingest are app write paths. Portable `db` access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from pydantic import BaseModel

from .. import corpus_service, corpus_writes, db
from ..auth import Principal, require_scope

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
