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

import sqlalchemy as sa

from . import corpus_writes, db, grants, sensitivity
from .auth import Principal
from .search_maps import ABSTRACTION_KINDS, PREFIX_TARGETS, SEARCH_TARGETS

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


def _record_pull(table: str, artifact_id, surface: str, query_text: str,
                 caller_id: str | None) -> None:
    if artifact_id is None or not db.has_table("pull_events"):
        return
    cols = db.columns("pull_events")
    values = {}
    for k, v in (("artifact_table", table), ("artifact_id", artifact_id),
                 ("surface", surface), ("query_text", query_text),
                 ("caller_id", caller_id), ("source_ip", source_ip_var.get())):
        if k in cols:
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
    return decision


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
           limit: int = 40, surface: str = "rest:/v1/search") -> dict:
    if not q or len(q) < 2:
        return {"ok": False, "error": "q must be at least 2 characters"}

    if kinds:
        requested = [k.strip() for k in kinds.split(",") if k.strip()]
        kinds_to_search = [k for k in requested if k in SEARCH_TARGETS]
        if not kinds_to_search:
            return {"ok": False, "error": "no recognized kinds; valid: "
                    + ",".join(sorted(SEARCH_TARGETS))}
    else:
        kinds_to_search = list(SEARCH_TARGETS.keys())

    limit_total = max(1, min(int(limit), 200))
    per_kind = 5
    like = f"%{q.lower()}%"
    cutoff = None
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))
                  ).strftime("%Y-%m-%d %H:%M:%S")

    caller = f"token:{principal.id}:{principal.name}"
    hits: list[dict] = []
    truncated = False

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

        for row in rows:
            rid = row.get("id")
            decision = _gate_decision(principal, table, row)
            if decision == "withheld":
                continue  # above the token's ceiling - not even surfaced
            extras = {k: row[k] for k in ("period_label", "kind", "confidence",
                                          "name", "direction") if row.get(k)}
            hits.append({
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
            if len(hits) >= limit_total:
                truncated = True
                break
        if truncated:
            break

    for h in hits:
        _record_pull(h["artifact_table"], h["artifact_id"], surface, q, caller)

    abstractions, gists, raw_refs, seen = [], [], [], set()
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
        elif h["kind"] in ABSTRACTION_KINDS:
            abstractions.append(h)
        else:
            abstractions.append(h)

    return {"ok": True, "query": q, "kinds_searched": kinds_to_search,
            "hits": hits, "abstractions": abstractions, "gists": gists,
            "raw_refs": raw_refs, "total": len(hits), "truncated": truncated}


def fetch(principal: Principal, token: str) -> dict:
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

    _record_pull(table, rid, "rest:/v1/item", token,
                 f"token:{principal.id}:{principal.name}")
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
]


def recent(principal: Principal, *, days: int = 7, limit: int = 40) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))
              ).strftime("%Y-%m-%d %H:%M:%S")
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
            })
    items.sort(key=lambda x: str(x["created_at"]), reverse=True)
    return {"ok": True, "days": days, "total": len(items[:limit]),
            "items": items[:limit]}


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
