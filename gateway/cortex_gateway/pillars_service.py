"""Pillar operations for the MCP surface: Projects, Rules, Skills.

Sits beside corpus_service.py. Reads run DIRECTLY over the read-only ATTACHed
corpus (cortex.projects, overseer.tech_rules / tech_skills / tech_skill_log,
overseer.project_summaries) but funnel through the SAME default-deny grant +
tier + category gate the Memory reads use (corpus_service.gate_decision) and
log a pull_event with an mcp:<tool> surface so the exfil monitor sees them.
Writes cannot touch the read-only attach, so they route to the co-located
core over localhost HTTP (corpus_writes), and every write self-checks the
connector:write scope (off by default), mirroring cortex_ingest.

People is deliberately NOT exposed here: it stays owner-only (Tory's call,
2026-07-21) because person_notes carry third-party PII that never consented
to an external AI surface.
"""
from __future__ import annotations

import logging

from . import corpus_service, corpus_writes, db, grants
from .auth import Principal
from .core_client import CoreWriteError, write_error_text

# Shown to a connector whose connection is not approved for writing.
_WRITE_DENIED = "write requires an approved connection"

log = logging.getLogger("cortex_gateway.pillars")


def _caller(p: Principal) -> str:
    # OPT-0: durable classified identity, shared with the corpus surface.
    return corpus_service.caller_identity(p)[0]


def _class(p: Principal) -> str:
    return corpus_service.caller_identity(p)[1]


def _visible(principal: Principal, table: str, row: dict) -> bool:
    """A structured pillar row is surfaced only when the shared gate returns
    'full'. These tables are untagged today (-> internal -> full), but the
    grant gate (default-deny for an unapproved connector) and any per-token
    category filter still bite here. We do not emit sanitized/title_only
    partials for structured rows: anything short of 'full' is dropped."""
    return corpus_service.gate_decision(principal, table, row) == "full"


def _cap(limit, default: int = 40, ceiling: int = 200) -> int:
    try:
        return max(1, min(int(limit or default), ceiling))
    except (TypeError, ValueError):
        return default


# ── Projects (cortex.projects + overseer.project_summaries) ───────────

_PROJECT_LIST_FIELDS = ("tag", "name", "status", "priority", "category",
                        "org_tag", "total_hours", "last_touched")
# `collaborators` is intentionally NOT exposed: it is a structured list of
# third-party names, i.e. People-pillar data, and People is owner-only.
_PROJECT_DETAIL_FIELDS = _PROJECT_LIST_FIELDS + (
    "description", "org_tag", "github_url", "created_at")
# `narrative` is omitted for the same reason: the Overseer's free-text project
# rollup can name third parties who never consented to an external AI surface.
# Only the numeric rollup stats cross the MCP surface.
_SUMMARY_FIELDS = ("session_count", "active_minutes_total", "total_minutes",
                   "cost_usd_estimate")


def projects_list(principal: Principal, *, status: str = "",
                  limit: int = 40) -> dict:
    if not db.has_table("projects"):
        return {"ok": True, "projects": [], "total": 0}
    where, params = "", {"lim": _cap(limit)}
    if status:
        where = "WHERE status = :status"
        params["status"] = status
    try:
        rows = db.fetchall(
            f"SELECT * FROM projects {where} "
            f"ORDER BY last_touched DESC LIMIT :lim", params)
    except Exception as e:
        log.warning("projects_list read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    out = []
    for r in rows:
        if not _visible(principal, "projects", r):
            continue
        # projects is tag-keyed (no integer id), so the tag rides in
        # artifact_id. SQLite stores it via type affinity (the corpus backend);
        # the exfil monitor counts per-caller volume, which is unaffected by the
        # id's type. On the retiring Azure SQL path the telemetry insert simply
        # no-ops (record_pull swallows it); the read itself never fails.
        corpus_service.record_pull("projects", r.get("tag"),
                                   "mcp:cortex_projects_list", status or "",
                                   _caller(principal),
                                   caller_class=_class(principal))
        out.append({k: r.get(k) for k in _PROJECT_LIST_FIELDS if k in r})
    return {"ok": True, "projects": out, "total": len(out)}


def project_get(principal: Principal, tag: str) -> dict:
    tag = (tag or "").strip()
    if not tag:
        return {"ok": False, "error": "tag is required"}
    if not db.has_table("projects"):
        return {"ok": False, "error": "not found", "tag": tag}
    try:
        row = db.fetchone("SELECT * FROM projects WHERE tag = :t", {"t": tag})
        # OPT-1 resolution: canonical tag always wins (checked above);
        # the alias map is consulted only on a canonical miss, so an
        # observed name (cwd basename, merged-away tag) returns the
        # same project as its canonical tag. Unknown stays not-found.
        if not row and db.has_table("project_aliases"):
            a = db.fetchone(
                "SELECT project_tag FROM project_aliases WHERE alias = :t",
                {"t": tag})
            if a:
                tag = a.get("project_tag")
                row = db.fetchone(
                    "SELECT * FROM projects WHERE tag = :t", {"t": tag})
    except Exception as e:
        log.warning("project_get read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    if not row or not _visible(principal, "projects", row):
        return {"ok": False, "error": "not found", "tag": tag}
    corpus_service.record_pull("projects", tag, "mcp:cortex_project_get", tag,
                               _caller(principal),
                               caller_class=_class(principal))
    project = {k: row.get(k) for k in _PROJECT_DETAIL_FIELDS if k in row}
    # Overseer rollup enrichment (interpretive; gate it the same way).
    if db.has_table("project_summaries"):
        try:
            s = db.fetchone(
                "SELECT * FROM project_summaries WHERE project = :t", {"t": tag})
        except Exception:
            s = None
        if s and _visible(principal, "project_summaries", s):
            project["summary"] = {k: s.get(k) for k in _SUMMARY_FIELDS
                                  if k in s}
    return {"ok": True, "project": project}


# ── Organizations (cortex.organizations, OPT-2) ───────────────────────

_ORG_FIELDS = ("tag", "name", "org_type", "my_role", "is_active",
               "is_default", "sort_order")


def orgs_list(principal: Principal) -> dict:
    """The org layer of the hierarchy, with per-org member counts and the
    untriaged count (projects whose org_tag is still ''). Read-only."""
    if not db.has_table("organizations"):
        return {"ok": True, "organizations": [], "total": 0, "untriaged": 0}
    try:
        rows = db.fetchall(
            "SELECT * FROM organizations ORDER BY sort_order, tag")
        counts = {r.get("org_tag") or "": r.get("n") for r in db.fetchall(
            "SELECT org_tag, COUNT(*) AS n FROM projects GROUP BY org_tag")}
    except Exception as e:
        log.warning("orgs_list read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    out = []
    for r in rows:
        if not _visible(principal, "organizations", r):
            continue
        corpus_service.record_pull("organizations", r.get("tag"),
                                   "mcp:cortex_orgs_list", "",
                                   _caller(principal),
                                   caller_class=_class(principal))
        org = {k: r.get(k) for k in _ORG_FIELDS if k in r}
        org["project_count"] = counts.get(r.get("tag"), 0)
        out.append(org)
    return {"ok": True, "organizations": out, "total": len(out),
            "untriaged": counts.get("", 0)}


def org_get(principal: Principal, tag: str) -> dict:
    """One organization plus its member projects. Read-only."""
    tag = (tag or "").strip()
    if not tag:
        return {"ok": False, "error": "tag is required"}
    if not db.has_table("organizations"):
        return {"ok": False, "error": "not found", "tag": tag}
    try:
        row = db.fetchone("SELECT * FROM organizations WHERE tag = :t",
                          {"t": tag})
    except Exception as e:
        log.warning("org_get read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    if not row or not _visible(principal, "organizations", row):
        return {"ok": False, "error": "not found", "tag": tag}
    corpus_service.record_pull("organizations", tag, "mcp:cortex_org_get",
                               tag, _caller(principal),
                               caller_class=_class(principal))
    org = {k: row.get(k) for k in _ORG_FIELDS if k in row}
    members = []
    try:
        prows = db.fetchall(
            "SELECT * FROM projects WHERE org_tag = :t "
            "ORDER BY last_touched DESC", {"t": tag})
        for p in prows:
            if _visible(principal, "projects", p):
                members.append(
                    {k: p.get(k) for k in _PROJECT_LIST_FIELDS if k in p})
    except Exception:
        pass
    org["projects"] = members
    org["project_count"] = len(members)
    return {"ok": True, "organization": org}


# ── Tasks (cortex.tasks, OPT-3: the shared memory layer) ─────────────
# FEDERATE posture (Tory 2026-07-26): the business tracker stays the
# separate execution tool; these tasks are context any agent can read or
# write so later agents pick it up. The overseer only curates; nothing
# here claims or executes.

_TASK_FIELDS = ("id", "uuid", "project_tag", "title", "details", "status",
                "priority", "due_date", "proposed", "source", "created_by",
                "external_ref", "completed_at", "created_at", "updated_at")


def tasks_list(principal: Principal, *, project: str = "",
               status: str = "", include_proposed: bool = False,
               limit: int = 40) -> dict:
    if not db.has_table("tasks"):
        return {"ok": True, "tasks": [], "total": 0}
    sql = "SELECT * FROM tasks"
    wheres, params = [], {}
    if project:
        wheres.append("project_tag = :p")
        params["p"] = project
    if status:
        wheres.append("status = :s")
        params["s"] = status
    if not include_proposed:
        wheres.append("proposed = 0")
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " ORDER BY priority ASC, updated_at DESC LIMIT :l"
    params["l"] = max(1, min(int(limit), 200))
    try:
        rows = db.fetchall(sql, params)
    except Exception as e:
        log.warning("tasks_list read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    out = []
    for r in rows:
        if not _visible(principal, "tasks", r):
            continue
        corpus_service.record_pull("tasks", r.get("id"),
                                   "mcp:cortex_tasks_list",
                                   project or status or "",
                                   _caller(principal),
                                   caller_class=_class(principal),
                                   parent_table="projects",
                                   parent_id=r.get("project_tag"))
        out.append({k: r.get(k) for k in _TASK_FIELDS if k in r})
    return {"ok": True, "tasks": out, "total": len(out)}


def task_add(principal: Principal, *, title: str, project: str,
             details: str = "", priority: int = 3,
             due_date: str = "") -> dict:
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    title, project = (title or "").strip(), (project or "").strip()
    if not title or not project:
        return {"ok": False, "error": "title and project are required"}
    values = {"title": title, "project_tag": project,
              "details": (details or "").strip(),
              "priority": int(priority), "due_date": (due_date or "").strip(),
              "source": "agent", "created_by": _caller(principal)}
    try:
        task_id = corpus_writes.upsert_task(values)
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    except Exception as e:
        # the core's write contract rejects bad projects/status with a
        # useful message; surface it verbatim
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "id": task_id, "title": title, "project": project}


def task_update(principal: Principal, *, id: int = 0, uuid: str = "",
                title: str = "", status: str = "", details: str = "",
                priority: int = 0, due_date: str = "") -> dict:
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    if not id and not uuid:
        return {"ok": False, "error": "id or uuid is required"}
    values: dict = {}
    if id:
        values["id"] = int(id)
    if uuid:
        values["uuid"] = uuid.strip()
    if title:
        values["title"] = title.strip()
    if status:
        values["status"] = status.strip()
    if details:
        values["details"] = details.strip()
    if priority:
        values["priority"] = int(priority)
    if due_date:
        values["due_date"] = due_date.strip()
    if set(values) <= {"id", "uuid"}:
        return {"ok": False, "error": "nothing to update"}
    try:
        task_id = corpus_writes.upsert_task(values)
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "id": task_id or values.get("id"),
            "updated": sorted(k for k in values if k not in ("id", "uuid"))}


# ── Rules (overseer.tech_rules) ───────────────────────────────────────

_RULE_FIELDS = ("id", "title", "rule", "stack", "situation", "status",
                "updated_at")


def rules_list(principal: Principal, *, status: str = "active",
               stack: str = "", limit: int = 40) -> dict:
    if not db.has_table("tech_rules"):
        return {"ok": True, "rules": [], "total": 0}
    clauses, params = [], {"lim": _cap(limit)}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if stack:
        clauses.append("LOWER(stack) LIKE :stack")
        params["stack"] = f"%{stack.lower()}%"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = db.fetchall(
            f"SELECT * FROM tech_rules {where} "
            f"ORDER BY updated_at DESC LIMIT :lim", params)
    except Exception as e:
        log.warning("rules_list read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    out = []
    for r in rows:
        if not _visible(principal, "tech_rules", r):
            continue
        corpus_service.record_pull("tech_rules", r.get("id"),
                                   "mcp:cortex_rules_list", stack or "",
                                   _caller(principal),
                                   caller_class=_class(principal))
        out.append({k: r.get(k) for k in _RULE_FIELDS if k in r})
    return {"ok": True, "rules": out, "total": len(out)}


# ── Skills (overseer.tech_skills + tech_skill_log) ────────────────────

_SKILL_FIELDS = ("id", "name", "proficiency", "summary", "tools", "updated_at")
_SKILL_LOG_FIELDS = ("id", "kind", "content", "project", "source", "created_at")


def skills_list(principal: Principal, *, limit: int = 40) -> dict:
    if not db.has_table("tech_skills"):
        return {"ok": True, "skills": [], "total": 0}
    try:
        rows = db.fetchall(
            "SELECT * FROM tech_skills ORDER BY LOWER(name) LIMIT :lim",
            {"lim": _cap(limit)})
    except Exception as e:
        log.warning("skills_list read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    out = []
    for r in rows:
        if not _visible(principal, "tech_skills", r):
            continue
        corpus_service.record_pull("tech_skills", r.get("id"),
                                   "mcp:cortex_skills_list", "",
                                   _caller(principal),
                                   caller_class=_class(principal))
        out.append({k: r.get(k) for k in _SKILL_FIELDS if k in r})
    return {"ok": True, "skills": out, "total": len(out)}


def skill_get(principal: Principal, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    if not db.has_table("tech_skills"):
        return {"ok": False, "error": "not found", "name": name}
    try:
        row = db.fetchone(
            "SELECT * FROM tech_skills WHERE LOWER(name) = :n",
            {"n": name.lower()})
    except Exception as e:
        log.warning("skill_get read failed: %s", e)
        return {"ok": False, "error": "read failed"}
    if not row or not _visible(principal, "tech_skills", row):
        return {"ok": False, "error": "not found", "name": name}
    corpus_service.record_pull("tech_skills", row.get("id"),
                               "mcp:cortex_skill_get", name,
                               _caller(principal),
                               caller_class=_class(principal))
    skill = {k: row.get(k) for k in _SKILL_FIELDS if k in row}
    entries = []
    if db.has_table("tech_skill_log"):
        try:
            logs = db.fetchall(
                "SELECT * FROM tech_skill_log WHERE skill_id = :sid "
                "ORDER BY created_at DESC LIMIT 50", {"sid": row.get("id")})
        except Exception:
            logs = []
        # Gate each child row too, matching project_get's summary gate. A no-op
        # while these tables are untagged, but it keeps the "every emitted row
        # funnels through _visible" invariant if they ever get tier/category.
        entries = [{k: e.get(k) for k in _SKILL_LOG_FIELDS if k in e}
                   for e in logs if _visible(principal, "tech_skill_log", e)]
    skill["log"] = entries
    return {"ok": True, "skill": skill}


# ── Writes (connector:write, off by default; route through the core) ──

def project_upsert(principal: Principal, *, tag: str, fields: dict) -> dict:
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    tag = (tag or "").strip()
    if not tag:
        return {"ok": False, "error": "tag is required"}
    try:
        corpus_writes.patch_project(tag, fields)
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    return {"ok": True, "tag": tag, "updated": sorted(fields)}


def rule_add(principal: Principal, *, title: str, rule: str,
             stack: str = "", situation: str = "") -> dict:
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    title, rule = (title or "").strip(), (rule or "").strip()
    if not title or not rule:
        return {"ok": False, "error": "title and rule are required"}
    try:
        out = corpus_writes.rule_add({
            "title": title, "rule": rule, "stack": (stack or "").strip(),
            "situation": (situation or "").strip(),
            "source": f"connector:{principal.name}"})
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    if not out.get("ok"):
        # Surface the core's own rejection instead of an ok with no id.
        return {"ok": False, "error": str(out.get("error") or
                                          "rule write failed")[:300]}
    # The core route nests the row: {"ok", "rule": {"id", ...}, "created"}.
    saved = out.get("rule") or {}
    return {"ok": True, "title": title, "id": saved.get("id"),
            "created": out.get("created")}


def rule_update(principal: Principal, *, title: str, rule: str = "",
                stack: str = "", situation: str = "",
                status: str = "") -> dict:
    """Amend or retire an existing rule by its (case-insensitive) title.
    Same core upsert as rule_add: only supplied fields overwrite, and
    status flips only when explicitly given, so retiring a rule never
    erases its text."""
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title is required"}
    status = (status or "").strip().lower()
    if status and status not in ("active", "retired"):
        return {"ok": False, "error": "status must be 'active' or 'retired'"}
    payload = {"title": title, "source": f"connector:{principal.name}"}
    for key, val in (("rule", rule), ("stack", stack),
                     ("situation", situation)):
        if val and val.strip():
            payload[key] = val.strip()
    if status:
        payload["status"] = status
    if set(payload) <= {"title", "source"}:
        return {"ok": False, "error":
                "nothing to change: pass rule, stack, situation, or status"}
    try:
        out = corpus_writes.rule_add(payload)
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    if not out.get("ok"):
        # e.g. updating a title that does not exist without rule text:
        # the core refuses to create a rule with no body.
        return {"ok": False, "error": str(out.get("error") or
                                          "rule update failed")[:300]}
    saved = out.get("rule") or {}
    return {"ok": True, "title": title, "id": saved.get("id"),
            "created": bool(out.get("created")),
            "status": saved.get("status")}


def org_upsert(principal: Principal, *, tag: str, fields: dict) -> dict:
    """Create or partially update an organization by tag, mirroring
    project_upsert. is_active=0 in fields retires the org from lists
    (soft; the row and its member links stay)."""
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    tag = (tag or "").strip()
    if not tag:
        return {"ok": False, "error": "tag is required"}
    try:
        corpus_writes.patch_org(tag, fields)
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    return {"ok": True, "tag": tag, "updated": sorted(fields)}


def skill_log(principal: Principal, *, skill: str, content: str,
              kind: str = "note", proficiency: str = "") -> dict:
    if not grants.can_write(principal):
        return {"ok": False, "error": _WRITE_DENIED}
    skill, content = (skill or "").strip(), (content or "").strip()
    if not skill or not content:
        return {"ok": False, "error": "skill and content are required"}
    payload = {"skill": skill, "content": content,
               "kind": (kind or "note").strip() or "note",
               "source": f"connector:{principal.name}"}
    if (proficiency or "").strip():
        payload["proficiency"] = proficiency.strip()
    try:
        out = corpus_writes.skill_log(payload)
    except CoreWriteError as e:
        return {"ok": False, "error": write_error_text(e)}
    # The core route nests the row: {"ok", "skill": {...}, "entry": {"id",
    # "skill_id", ...}, "skill_created"}.
    entry = out.get("entry") or {}
    res = {"ok": True, "skill": skill, "skill_created": out.get("skill_created")}
    if entry.get("id") is not None:
        res["entry_id"] = entry["id"]
    if entry.get("skill_id") is not None:
        res["skill_id"] = entry["skill_id"]
    return res
