"""Sync v2 endpoints - the Gateway transport of the ratified contract
(cortex-desktop/docs/SYNC_CONTRACT_DRAFT.md, v2 2026-06-10).

POST /v1/sync/push    uuid-idempotent row upload (phone-authored kinds)
POST /v1/sync/pull    opaque-cursor download (interpretive kinds)
GET  /v1/sync/status  counts + newest per pullable kind

Same JSON bodies as the BLE bridge's CMD:sync_* lines - one engine, two
transports. Scope `app` (the phone); connectors don't sync.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from pydantic import BaseModel

from .. import corpus_writes, db
from ..auth import Principal, require_scope

router = APIRouter(prefix="/v1/sync", tags=["sync"])

_app = require_scope("app")

# kind -> (cursor prefix, pull columns)
PULL_KINDS: dict[str, tuple[str, list[str]]] = {
    "summaries_gist": ("g", ["id", "period_label", "body", "confidence", "created_at"]),
    "temporal_narratives": ("nar", ["id", "kind", "period_label", "period_start",
                                    "period_end", "narrative", "created_at"]),
}

# kind -> insertable columns (phone-authored, append-only)
PUSH_KINDS: dict[str, list[str]] = {
    "human_journal_entries": ["text", "entry_type", "created_at"],
    "notes": ["content", "note_type", "project", "tags", "created_at"],
    # Cloud P5 (Tory's decision, 2026-07-21): the former piOnly kinds
    # route through the cloud now that the co-located core IS home.
    # Transport-only change: none of these tables are in the connector
    # read surface, so external AIs still cannot see them. Mirrors the
    # core sync plugin's PUSH_KINDS; the core re-validates on forward.
    "device_notifications": ["app", "title", "body", "posted_at",
                             "created_at"],
    "person_notes": ["person_id", "body", "note_kind", "modality",
                     "provenance", "created_at", "local_created_at",
                     "created_by_agent"],
    "voice_chat_turns": ["chat_id", "chat_title", "role", "content",
                         "model", "created_at"],
    "time_entries": ["project_tag", "org_tag", "activity_type",
                     "description", "started_at", "duration_minutes",
                     "created_at"],
    "notification_responses": ["notification_id", "action_kind",
                               "action_label", "response_payload_json",
                               "taken_at"],
    "interaction_feedback": ["target_kind", "target_id", "rating",
                             "note", "context_json", "source",
                             "created_at"],
}


class PushIn(BaseModel):
    device: str
    kind: str
    rows: list[dict]


@router.post("/push")
def push(body: PushIn, _: Principal = Depends(_app)):
    cols = PUSH_KINDS.get(body.kind)
    if cols is None:
        return {"ok": False, "error": f"unknown push kind: {body.kind}"}
    if corpus_writes.routed():
        # ATTACH mode: the corpus is read-only here. Forward the batch
        # verbatim to the co-located core's sync plugin - the same
        # ratified contract v2 on both ends, with uuid dedup
        # (sync_row_map) living core-side. Response shape is identical,
        # so it passes straight through to the phone.
        return corpus_writes.sync_push(
            body.device, body.kind, [dict(r) for r in body.rows])
    accepted, dupes, rejected, ids = 0, 0, [], {}
    for row in body.rows:
        uid = row.get("id")
        if not uid or not isinstance(uid, str):
            rejected.append({"id": uid, "reason": "missing uuid id"})
            continue
        existing = db.fetchone(
            "SELECT remote_id FROM sync_row_map WHERE uuid = :u", {"u": uid})
        if existing:
            dupes += 1
            ids[uid] = existing["remote_id"]
            continue
        values = {c: row.get(c) for c in cols if row.get(c) is not None}
        if body.kind == "notes":
            values.setdefault("source", "mobile")
        try:
            remote_id = db.insert(body.kind, values)
        except Exception as e:
            rejected.append({"id": uid, "reason": str(e)[:200]})
            continue
        db.insert("sync_row_map", {
            "uuid": uid, "kind": body.kind, "device": body.device,
            "remote_id": remote_id})
        ids[uid] = remote_id
        accepted += 1
    return {"ok": True, "kind": body.kind, "accepted": accepted,
            "dupes": dupes, "rejected": rejected, "ids": ids}


class PullIn(BaseModel):
    device: str
    kind: str
    cursor: str = ""
    limit: int = 10


@router.post("/pull")
def pull(body: PullIn, _: Principal = Depends(_app)):
    spec = PULL_KINDS.get(body.kind)
    if spec is None:
        return {"ok": False, "error": f"unknown pull kind: {body.kind}"}
    prefix, cols = spec
    last_id = 0
    if body.cursor.startswith(prefix + ":"):
        try:
            last_id = int(body.cursor.split(":", 1)[1])
        except ValueError:
            last_id = 0
    limit = max(1, min(body.limit, 50))
    if not db.has_table(body.kind):
        return {"ok": True, "kind": body.kind, "rows": [], "more": False,
                "next_cursor": body.cursor}
    rows = db.fetchall(
        f"SELECT {', '.join(cols)} FROM {body.kind} WHERE id > :last "
        f"ORDER BY id", {"last": last_id})[:limit]
    for r in rows:  # datetimes -> strings for JSON
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = str(v)
    more = False
    if rows:
        more = db.fetchone(
            f"SELECT 1 AS x FROM {body.kind} WHERE id > :last",
            {"last": rows[-1]["id"]}) is not None
    return {"ok": True, "kind": body.kind, "rows": rows, "more": more,
            "next_cursor": f"{prefix}:{rows[-1]['id']}" if rows else body.cursor}


@router.get("/status")
def status(_: Principal = Depends(_app)):
    counts, newest = {}, {}
    for kind in PULL_KINDS:
        if not db.has_table(kind):
            counts[kind] = 0
            newest[kind] = None
            continue
        counts[kind] = db.fetchone(f"SELECT count(*) AS c FROM {kind}")["c"]
        row = db.fetchone(
            f"SELECT created_at FROM {kind} ORDER BY id DESC")
        newest[kind] = str(row["created_at"]) if row else None
    return {"ok": True, "counts": counts, "newest": newest}
