"""Corpus operations shared by REST (/v1/search, /item, /recent, /ingest) and
MCP (search/fetch + cortex_*). Portable SQLAlchemy Core implementation over the
single canonical database - runs identically on SQLite (dev) and Azure SQL.

This replaces the previous dependency on cortex-core's SQLite-only
`corpus.search_corpus` / `detail.resolve_detail`. The layered-return shape
(abstractions → gists → raw_refs) is preserved.
"""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import sqlalchemy as sa

from . import corpus_writes, db, grants, sensitivity
from .auth import Principal
from .config import get_settings
from .core_client import CoreWriteError
from .search_maps import (ABSTRACTION_KINDS, PREFIX_TARGETS, SEARCH_TARGETS,
                          resolve_kind)

log = logging.getLogger("cortex_gateway.corpus")

_TIME_COLS = ("created_at", "written_at", "observed_at")


def _snippet(row: dict, body_cols: list[str], q: str) -> str:
    ql = q.lower()
    for c in body_cols:
        val = row.get(c) or ""
        if not val:
            continue
        idx = val.lower().find(ql)
        if idx == -1:
            continue
        start = max(0, idx - 80)
        end = min(len(val), idx + len(q) + 120)
        return ("…" if start > 0 else "") + val[start:end] + ("…" if end < len(val) else "")
    for c in body_cols:
        v = row.get(c) or ""
        if v:
            return v[:200] + ("…" if len(v) > 200 else "")
    return ""


# Source IP of the current request, bound by the middleware in app.py so
# _record_pull can stamp it without threading request through every signature.
source_ip_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cortex_source_ip", default=None)


@lru_cache(maxsize=256)
def _connector_handle(client_id: str) -> str:
    """Durable handle for a connector: grant name + redirect host survive
    the client_id churn of repeated OAuth registrations (OPT-0). Cached per
    process; a grant rename takes effect on the next deploy/restart."""
    g = grants.grant_for(client_id)
    if g:
        return "connector:{}|{}".format(g.get("name") or client_id,
                                        g.get("redirect_host") or "")
    return f"connector:{client_id}"


def caller_identity(principal: Principal | None) -> tuple[str, str]:
    """(caller_id, caller_class) for pull telemetry (OPT-0).

    Every gateway row gets an EXPLICIT class; the core's empty-means-organic
    convention is never used here because it inverts on gateway rows.
    Classes align with the core scorer's weighting: organic-external counts
    full; user-probe and automation are discounted.
    """
    if principal is None:
        return ("", "unclassified")
    is_connector = (principal.kind == "oauth"
                    or "connector:read" in principal.scopes
                    or "connector:write" in principal.scopes)
    if is_connector:
        return (_connector_handle(principal.client_id or principal.name),
                "organic-external")
    if "hub" in principal.scopes or "app" in principal.scopes:
        return (f"owner:{principal.name}", "user-probe:owner-app")
    if "admin" in principal.scopes:
        return (f"admin:{principal.name}", "automation:admin")
    return (f"token:{principal.id}:{principal.name}", "automation:token")


def _record_pull(table: str, artifact_id, surface: str, query_text: str,
                 caller_id: str | None, *, caller_class: str = "",
                 parent_table: str | None = None,
                 parent_id=None) -> None:
    if artifact_id is None or not db.has_table("pull_events"):
        return
    cols = db.columns("pull_events")
    values = {}
    for k, v in (("artifact_table", table), ("artifact_id", artifact_id),
                 ("surface", surface), ("query_text", query_text),
                 ("caller_id", caller_id), ("caller_class", caller_class),
                 ("parent_artifact_table", parent_table),
                 ("parent_artifact_id", parent_id),
                 ("source_ip", source_ip_var.get())):
        if k in cols and v is not None:
            values[k] = v
    try:
        db.insert("pull_events", values)
    except Exception as e:  # never fail a read because of telemetry
        log.warning("pull_event insert failed: %s", e)


# ── Sensitivity gating (Slice 13) - enforced on every read below ──────
# Raw-layer tables: withheld from connectors by default (none are in the
# Gateway search/fetch maps today, but fetch() is prefix-driven so guard it).
_RAW_TABLES = {"imported_sessions", "files"}
# Per-row tier columns cortex-core stamps on tiered content. Interpretive
# tables are untagged today -> default 'internal' -> full (no regression).
_TIER_COLS = ("sensitivity_tier", "tier", "sensitivity")
# Body-preview markers for gated hits (ASCII; the full sanitized-body marker
# lives in sensitivity.apply()).
_GATE_SNIPPET = {"sanitized": "[confidential: sanitized at gateway]",
                 "title_only": "[gated: body withheld]"}


def _row_tier(row: dict):
    for c in _TIER_COLS:
        if row.get(c):
            return row[c]
    return None


def _gate_decision(principal: Principal, table: str, row: dict) -> str:
    """Effective read decision for one row + caller. Connector access grant
    FIRST (default deny), then the tier ceiling (sensitivity.decide), then an
    optional per-token category allow-list. Returns
    full | sanitized | title_only | withheld."""
    # Per-connection grant: an unapproved connector reads nothing at all,
    # independent of row tiers. Approved (active + full) connectors + app tokens
    # fall through. See grants.py / docs/CONNECTOR_GRANTS_DESIGN.md.
    if principal.is_connector and not grants.has_full_access(principal):
        return "withheld"
    decision = sensitivity.decide(
        _row_tier(row), principal.max_tier,
        is_raw=table in _RAW_TABLES, is_connector=principal.is_connector)
    if decision != "withheld" and principal.category_filter:
        rc = (row.get("category") or "").strip()
        if rc and rc not in principal.category_filter:
            return "withheld"
    # Notes carry no per-row tier column, so sensitivity.decide() lands them at
    # 'full' for everyone. Give the owner one env-var lever to clamp raw
    # captures for CONNECTORS specifically, without touching what their own
    # phone/Hub/desktop can read and without a code change or deploy.
    if decision != "withheld" and table == "notes" and principal.is_connector:
        policy = get_settings().notes_connector_policy
        if policy != "full":
            decision = _NOTE_POLICY_RANK_MIN(decision, policy)
    return decision


# Ordered most to least permissive; clamping means "no better than policy".
_DECISION_ORDER = ("full", "sanitized", "title_only", "withheld")


def _NOTE_POLICY_RANK_MIN(decision: str, policy: str) -> str:
    try:
        return _DECISION_ORDER[max(_DECISION_ORDER.index(decision),
                                   _DECISION_ORDER.index(policy))]
    except ValueError:
        return decision


# Public names for cross-module callers (pillars_service reuses the exact
# same default-deny grant + tier + category gate and pull-logging). The
# underscored originals stay for existing call sites and tests.
gate_decision = _gate_decision
record_pull = _record_pull


def _redact_row(decision: str, row: dict, body_cols: list, title_col) -> dict:
    """Strip body content from a fetched row per the gate decision."""
    if decision == "withheld":
        return {"id": row.get("id"), "gated": True, "gate": "withheld"}
    shaped = sensitivity.apply(
        decision, title=(row.get(title_col) if title_col else "") or "", body="")
    for bc in body_cols:
        if bc in row and bc != title_col:
            row[bc] = shaped["body"]
    row["gated"] = True
    row["gate"] = decision
    return row


def search(principal: Principal, q: str, *, kinds: str = "", days: int = 0,
           limit: int = 40, per_kind: int = 5,
           surface: str = "rest:/v1/search") -> dict:
    if not q or len(q) < 2:
        return {"ok": False, "error": "q must be at least 2 characters"}

    if kinds:
        requested = [resolve_kind(k) for k in kinds.split(",") if k.strip()]
        # dedupe while preserving caller order (aliases can collide)
        kinds_to_search = list(dict.fromkeys(
            k for k in requested if k in SEARCH_TARGETS))
        if not kinds_to_search:
            return {"ok": False, "error": "no recognized kinds; valid: "
                    + ",".join(sorted(SEARCH_TARGETS))}
    else:
        kinds_to_search = list(SEARCH_TARGETS.keys())

    limit_total = max(1, min(int(limit), 200))
    per_kind = max(1, min(int(per_kind), 50))
    like = f"%{q.lower()}%"
    cutoff = None
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))
                  ).strftime("%Y-%m-%d %H:%M:%S")

    caller, caller_class = caller_identity(principal)

    # PHASE 1: query EVERY requested kind before serving any of them.
    #
    # This used to be one loop that appended straight into `hits` and broke out
    # of the OUTER kind loop once limit_total was reached. With the default
    # limit of 40 and 11 kinds at 5 rows each, any query matching the first
    # eight kinds exhausted the budget and the last kinds were never queried at
    # all. Iteration order put user_note and human, the only two tables the
    # OWNER authored, at the back of that line. Collect first, ration second.
    by_kind: dict[str, list[dict]] = {}
    for kind in kinds_to_search:
        table, body_cols, prefix, label = SEARCH_TARGETS[kind]
        if not db.has_table(table):
            continue
        t = db.table(table)
        tcols = db.columns(table)
        body_cols = [c for c in body_cols if c in tcols]
        if not body_cols:
            continue
        conds = [sa.func.lower(t.c[c]).like(like) for c in body_cols]
        stmt = sa.select(t).where(sa.or_(*conds))
        if cutoff:
            tcol = next((c for c in _TIME_COLS if c in tcols), None)
            if tcol:
                stmt = stmt.where(t.c[tcol] >= cutoff)
        order_col = t.c["id"] if "id" in tcols else list(t.c)[0]
        stmt = stmt.order_by(order_col.desc()).limit(per_kind)

        try:
            with db.engine().connect() as c:
                rows = [dict(r) for r in c.execute(stmt).mappings()]
        except Exception as e:
            log.warning("search target %s failed: %s", table, e)
            continue

        shaped: list[dict] = []
        for row in rows:
            rid = row.get("id")
            decision = _gate_decision(principal, table, row)
            if decision == "withheld":
                continue  # above the token's ceiling - not even surfaced
            extras = {k: row[k] for k in ("period_label", "kind", "confidence",
                                          "name", "direction", "note_type",
                                          "source", "project") if row.get(k)}
            shaped.append({
                "token": f"{prefix}:{rid}" if rid is not None else None,
                "kind": label,
                "artifact_table": table,
                "artifact_id": rid,
                "snippet": (_snippet(row, body_cols, q) if decision == "full"
                            else _GATE_SNIPPET[decision]),
                "created_at": (row.get("created_at") or row.get("written_at")
                              or row.get("observed_at") or ""),
                "gated": decision != "full",
                "extras": extras,
            })
        if shaped:
            by_kind[kind] = shaped

    # PHASE 2: round-robin so every kind that matched gets represented before
    # any kind gets a second row. Truncation now costs each kind its tail
    # rather than costing the last kinds their existence.
    hits: list[dict] = []
    truncated = False
    depth = 0
    while len(hits) < limit_total:
        served_this_pass = False
        for kind in kinds_to_search:
            rows = by_kind.get(kind)
            if not rows or depth >= len(rows):
                continue
            hits.append(rows[depth])
            served_this_pass = True
            if len(hits) >= limit_total:
                break
        if not served_this_pass:
            break
        depth += 1
    if sum(len(v) for v in by_kind.values()) > len(hits):
        truncated = True

    for h in hits:
        _record_pull(h["artifact_table"], h["artifact_id"], surface, q, caller,
                     caller_class=caller_class)

    abstractions, gists, notes, raw_refs, seen = [], [], [], [], set()
    for h in hits:
        if h["kind"] == "gist":
            period = (h.get("extras") or {}).get("period_label") or ""
            raw_id = period if ":" in period else None
            g = dict(h)
            if raw_id:
                g["raw_id"] = raw_id
                if raw_id not in seen:
                    raw_refs.append({"raw_id": raw_id,
                                     "linked_gist_token": h.get("token"),
                                     "note": "Layer 3 raw source; sensitivity "
                                             "rules apply at fetch time."})
                    seen.add(raw_id)
            gists.append(g)
        elif h["kind"] == "user_note":
            # Own layer: a note is raw owner input, not interpretation. Callers
            # that only read abstractions/gists are unaffected by this key.
            notes.append(h)
        elif h["kind"] in ABSTRACTION_KINDS:
            abstractions.append(h)
        else:
            abstractions.append(h)

    return {"ok": True, "query": q, "kinds_searched": kinds_to_search,
            "hits": hits, "abstractions": abstractions, "gists": gists,
            "notes": notes, "raw_refs": raw_refs, "total": len(hits),
            "truncated": truncated}


def fetch(principal: Principal, token: str,
          surface: str = "rest:/v1/item") -> dict:
    if not token or ":" not in token:
        return {"ok": False, "error": "token must look like '<prefix>:<id>'",
                "token": token}
    prefix, _, rest = token.partition(":")
    target = PREFIX_TARGETS.get(prefix.strip())
    if not target:
        return {"ok": False, "error": f"unknown token prefix '{prefix}'",
                "token": token}
    table, body_cols, title_col, label = target
    if not db.has_table(table):
        return {"ok": False, "error": "not found", "token": token}
    try:
        rid = int(rest)
    except ValueError:
        return {"ok": False, "error": "token id must be an integer", "token": token}

    row = db.fetchone(f"SELECT * FROM {table} WHERE id = :id", {"id": rid})
    if not row:
        return {"ok": False, "error": "not found", "token": token, "type": label}

    # OPT-0 parent stamp: a fetched gist attributes upward to its project
    # (the abstraction one layer above), so drill-past at the
    # summary-to-gist layer is measurable. Stateless reverse lookup; no
    # search context needed.
    parent_table = parent_id = None
    if table == "summaries_gist" and row.get("project_tag"):
        parent_table, parent_id = "projects", row.get("project_tag")
    caller, caller_class = caller_identity(principal)
    _record_pull(table, rid, surface, token, caller,
                 caller_class=caller_class,
                 parent_table=parent_table, parent_id=parent_id)
    decision = _gate_decision(principal, table, row)
    if decision != "full":
        row = _redact_row(decision, dict(row), body_cols, title_col)
    return {"ok": True, "token": token, "type": label, "primary": row,
            "gated": decision != "full", "gate": decision, "next_tokens": []}


_RECENT_SOURCES = [
    ("summaries_gist", "body", "g", "gist"),
    ("overseer_journal", "body", "j", "journal_entry"),
    ("temporal_narratives", "narrative", "nar", "temporal_narrative"),
    ("open_questions", "question", "q", "question"),
    ("patterns", "body", "p", "pattern"),
    # The owner's own material. Both were missing, so "what changed recently"
    # answered with AI output only: on a day the owner wrote three notes, this
    # returned nothing but a narrative saying the day was quiet.
    ("notes", "content", "un", "user_note"),
    ("human_journal_entries", "text", "hj", "human_journal_entry"),
]


def recent(principal: Principal, *, days: int = 7, limit: int = 40,
           surface: str = "rest:/v1/recent") -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))
              ).strftime("%Y-%m-%d %H:%M:%S")
    caller, caller_class = caller_identity(principal)
    items: list[dict] = []
    for table, body_col, prefix, kind in _RECENT_SOURCES:
        if not db.has_table(table):
            continue
        tcols = db.columns(table)
        if body_col not in tcols:
            continue
        tcol = next((c for c in _TIME_COLS if c in tcols), None)
        t = db.table(table)
        stmt = sa.select(t)
        if tcol:
            stmt = stmt.where(t.c[tcol] >= cutoff).order_by(t.c[tcol].desc())
        else:
            stmt = stmt.order_by(t.c["id"].desc())
        stmt = stmt.limit(int(limit))
        try:
            with db.engine().connect() as c:
                rows = [dict(r) for r in c.execute(stmt).mappings()]
        except Exception:
            continue
        for row in rows:
            decision = _gate_decision(principal, table, row)
            if decision == "withheld":
                continue
            body = row.get(body_col) or ""
            summary = (body[:240] + ("…" if len(body) > 240 else "")
                       if decision == "full" else _GATE_SNIPPET[decision])
            items.append({
                "token": f"{prefix}:{row.get('id')}",
                "kind": kind,
                "summary": summary,
                "created_at": (row.get(tcol) if tcol else "") or "",
                "gated": decision != "full",
                "_table": table,
            })
    items.sort(key=lambda x: str(x["created_at"]), reverse=True)
    served = items[:limit]
    # OPT-0: recent() was the one unlogged read surface despite being the
    # recommended bootstrap tool; log exactly what was served.
    for it in served:
        _record_pull(it.pop("_table"), int(it["token"].split(":", 1)[1]),
                     surface, f"days={days}", caller,
                     caller_class=caller_class)
    for it in items[limit:]:
        it.pop("_table", None)
    return {"ok": True, "days": days, "total": len(served),
            "items": served}


def ingest(principal: Principal, *, content: str, kind: str = "note",
           tags: str | None = None, project: str | None = None) -> dict:
    if not content or not content.strip():
        return {"ok": False, "error": "content is required"}
    source = "cortex" if principal.has("app") else "ai-generated"
    new_id = corpus_writes.insert_note({
        "content": content, "note_type": kind or "note",
        "project": project or "", "tags": tags or "", "source": source,
    })
    return {"ok": True, "note_id": new_id, "note_type": kind or "note",
            "project": project or "", "source": source}


def note_update(principal: Principal, *, id: int, content: str = "",
                tags: str = "", project: str = "") -> dict:
    """Correct a note the AI side wrote. Only rows with source
    'ai-generated' (what connectors and agents produce, including
    cortex_ingest) are editable here: a connector fixes its own output,
    it does not rewrite the owner's words. Owner captures stay
    read-only over MCP; amending one means writing a correcting note.
    """
    if not grants.can_write(principal):
        return {"ok": False, "error": "write requires an approved connection"}
    try:
        nid = int(id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "id must be an integer note id"}
    row = db.fetchone("SELECT id, source FROM notes WHERE id = :id",
                      {"id": nid})
    if not row:
        return {"ok": False, "error": f"note {nid} not found"}
    if (row.get("source") or "") != "ai-generated":
        return {"ok": False, "error": (
            f"note {nid} is one of the owner's own captures and is "
            "read-only over MCP. Only AI-written notes (source "
            "'ai-generated') can be edited. To amend it, write a "
            f"correcting note with cortex_ingest that references un:{nid}.")}
    fields: dict = {}
    if content and content.strip():
        fields["content"] = content
    if tags and tags.strip():
        fields["tags"] = tags.strip()
    if project and project.strip():
        fields["project"] = project.strip()
    if not fields:
        return {"ok": False,
                "error": "nothing to change: pass content, tags, or project"}
    try:
        corpus_writes.patch_note(nid, fields)
    except CoreWriteError:
        return {"ok": False, "error": "core unavailable for write"}
    return {"ok": True, "note_id": nid, "updated": sorted(fields)}
