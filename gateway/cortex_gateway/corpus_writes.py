"""Corpus write routing - one write path per deployment shape.

In the ATTACH topology (docs/CLOUD_MIGRATION.md P2) the corpus files are
attached read-only, so the gateway CANNOT write them: every corpus write
routes to the co-located core over HTTP (the corpus's single writer).
In the legacy shapes (single-file SQLite dev, Azure SQL via DB_URL) the
original direct DB writes run byte-for-byte until those modes retire.

Endpoints keep shaping their values exactly as before and hand the final
column dict here; this module only decides WHERE the write lands.

Core surfaces used (mapped 2026-07-20 scout):
  POST /api/cmd            command=upsert - partial-aware upsert_row for
                           the whitelisted spine tables (notes, projects,
                           people, time_entries). Full column control
                           including source/created_at; new-row inserts
                           return lastrowid inside 'RSP:upsert:{json}'.
  POST /plugins/sync/push  ratified sync contract v2 (same body as the
                           gateway's own /v1/sync/push): uuid-idempotent,
                           core-side sync_row_map dedup, raw column
                           passthrough for human_journal_entries.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from . import db
from .core_client import CoreWriteError, core

log = logging.getLogger("cortex_gateway.corpus_writes")


def routed() -> bool:
    """True when corpus writes must go through the core (ATTACH mode)."""
    return db.is_attach_mode()


def _utcnow() -> str:
    # Matches the storage format of SQLite's datetime('now') /
    # CURRENT_TIMESTAMP so rows are indistinguishable from local writes.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _cmd(command: str, payload: dict) -> str:
    """POST /api/cmd; return the protocol response string. ERR: responses
    become CoreWriteError (the app maps that to a 502)."""
    body = core().post("/api/cmd", {"command": command, "payload": payload})
    resp = str(body.get("response") or "")
    if resp.startswith("ERR:"):
        raise CoreWriteError(resp)
    return resp


def _rsp_json(resp: str, cmd: str) -> dict:
    prefix = f"RSP:{cmd}:"
    if not resp.startswith(prefix):
        raise CoreWriteError(f"unexpected core response: {resp[:80]}")
    try:
        return json.loads(resp[len(prefix):])
    except ValueError as e:
        raise CoreWriteError(f"unparseable core response: {e}") from e


def _upsert(table: str, values: dict):
    """Route one spine-table write through the core's partial-aware
    upsert. Returns the id the core reports (lastrowid for new auto-PK
    rows, the PK value otherwise)."""
    data = _rsp_json(_cmd("upsert", {"table": table, "data": values}),
                     "upsert")
    return data.get("id")


# ── Spine tables (cortex.db) ──────────────────────────────────────────


def insert_note(values: dict) -> int:
    if routed():
        return int(_upsert("notes", values))
    return db.insert("notes", values)


def insert_time_entry(values: dict) -> int:
    if routed():
        return int(_upsert("time_entries", values))
    return db.insert("time_entries", values)


def insert_person(values: dict) -> str:
    # values includes the caller-generated TEXT id (slug); the endpoint
    # keeps its collision logic via read-only lookups.
    if routed():
        return str(_upsert("people", values))
    return db.insert("people", values)


def insert_project(values: dict) -> None:
    # Create-only in intent: the endpoint 409s on an existing tag
    # first. KNOWN RESIDUAL (review 2026-07-20): a concurrent create
    # of the same tag inside the check-to-write window lands in the
    # core upsert's UPDATE branch (silent overwrite) where legacy
    # SQLite raised a PK violation. Accepted for the single-owner
    # topology; revisit if the write path ever multi-tenants.
    if routed():
        _upsert("projects", values)
        return
    db.insert("projects", values)


def upsert_task(values: dict):
    """OPT-3: route a task write through the core's guarded upsert (the
    write contract validates project_tag, status vocabulary, and uuid
    idempotency core-side). Returns the task id the core reports. The
    legacy single-file path fills the NOT NULL uuid itself since no core
    contract runs there."""
    if routed():
        return _upsert("tasks", values)
    values = dict(values)
    if values.get("id"):
        tid = values.pop("id")
        values["updated_at"] = _utcnow()
        sets = ", ".join(f"{k} = :{k}" for k in values)
        db.execute(f"UPDATE tasks SET {sets} WHERE id = :id",
                   {**values, "id": tid})
        return tid
    # Legacy parity with the core's uuid idempotency (OPT-9 fix): a
    # write carrying an existing uuid is an UPDATE of that row, matching
    # routed-mode semantics. Without this, PATCH /v1/tasks/{uuid} in
    # single-file dev mode became an INSERT missing NOT NULL columns.
    if values.get("uuid"):
        hit = db.fetchone("SELECT id FROM tasks WHERE uuid = :u",
                          {"u": values["uuid"]})
        if hit:
            uid = values.pop("uuid")
            values["updated_at"] = _utcnow()
            sets = ", ".join(f"{k} = :{k}" for k in values)
            db.execute(f"UPDATE tasks SET {sets} WHERE uuid = :u",
                       {**values, "u": uid})
            return hit["id"]
    values.setdefault("uuid", str(uuid.uuid4()))
    return db.insert("tasks", values)


def assign_agent(payload: dict) -> dict:
    """OPT-8: owner-assigned agent handle at connection approval. The
    agents table is deliberately NOT in the generic upsert whitelist
    (OPT_PLAN 7.2 V2-FIX: no relay table joins WRITABLE_TABLES before
    the OPT-11 write contract), so the write goes through the core's
    dedicated guarded agent_assign command. Requires the co-located
    core; grants.approve skips the call in legacy single-file mode."""
    return _rsp_json(_cmd("agent_assign", payload), "agent_assign")


def patch_project(tag: str, fields: dict) -> None:
    """Partial update. The core's upsert_row updates ONLY the supplied
    columns when the PK row exists (never CMD:project_upsert here - that
    one is a full overwrite that would reset omitted fields)."""
    if routed():
        _upsert("projects", {"tag": tag, **fields,
                             "last_touched": _utcnow()})
        return
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    db.execute(
        f"UPDATE projects SET {sets}, last_touched = CURRENT_TIMESTAMP "
        f"WHERE tag = :tag", {**fields, "tag": tag})


# ── Interpretive tables (overseer.db) ─────────────────────────────────


def insert_journal(values: dict) -> int:
    """human_journal_entries lives in overseer.db, outside CMD:upsert's
    whitelist. Route through the core's sync push (raw column
    passthrough preserves entry_type verbatim, e.g. 'reflection') with a
    gateway-minted uuid."""
    if routed():
        uid = str(uuid.uuid4())
        out = core().post("/plugins/sync/push", {
            "device": "gateway", "kind": "human_journal_entries",
            "rows": [{"id": uid, **values}]})
        rid = (out.get("ids") or {}).get(uid)
        if rid is None:
            rej = out.get("rejected") or []
            raise CoreWriteError(
                f"journal write rejected: {rej[0]['reason'][:120]}"
                if rej else "journal write returned no id")
        return int(rid)
    return db.insert("human_journal_entries", values)


def rule_add(values: dict) -> dict:
    """tech_rules lives in overseer.db, outside CMD:upsert's whitelist and
    owned solely by the core. Route the upsert-on-title through the overseer
    plugin route (same "POST to a core plugin route" shape as insert_journal).
    Returns the core's response dict ({ok, id, ...})."""
    if not routed():
        raise CoreWriteError("rule writes require the co-located core")
    return core().post("/plugins/overseer/rules/add", values)


def skill_log(values: dict) -> dict:
    """tech_skills + tech_skill_log live in overseer.db. Append a log entry
    (creating the skill header on first mention) through the overseer plugin
    route. Returns the core's response dict."""
    if not routed():
        raise CoreWriteError("skill writes require the co-located core")
    return core().post("/plugins/overseer/skills/log", values)


# ── Sync forwarding (ratified contract v2) ────────────────────────────


def sync_push(device: str, kind: str, rows: list[dict]) -> dict:
    """Forward a phone push batch verbatim to the core's sync plugin -     same contract on both ends, so the response passes straight through.
    Dedup (sync_row_map) is core-side; the gateway keeps no copy."""
    return core().post("/plugins/sync/push",
                       {"device": device, "kind": kind, "rows": rows})


def sync_pull(device: str, kind: str, cursor: str, limit: int) -> dict:
    """Forward a phone pull to the core's sync plugin, for kinds whose
    semantics live core-side (the Bell is a mutable SNAPSHOT with an
    answerable-set filter, which the gateway's insert-only cursor reads
    cannot represent). Same contract both ends; response passes through."""
    return core().post("/plugins/sync/pull",
                       {"device": device, "kind": kind,
                        "cursor": cursor, "limit": limit})
