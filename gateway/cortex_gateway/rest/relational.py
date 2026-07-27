"""Relational CRUD over the canonical store - projects, notes, people, time.

Columns match the canonical CortexDB schema (projects keyed by `tag`, notes
carry a `project` text column, people use a TEXT id). Portable SQLAlchemy access
via the `db` helpers, so this runs on SQLite (dev) and Azure SQL (prod). All
require the `app` scope; writes go through the Gateway (locked write-path).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from pydantic import BaseModel

from .. import corpus_writes, db
from ..auth import Principal, require_scope

router = APIRouter(prefix="/v1", tags=["relational"])

_app = require_scope("app")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "item"


# ── Projects (PK = tag) ───────────────────────────────────────────────


class ProjectIn(BaseModel):
    tag: str | None = None
    name: str | None = None
    status: str | None = None
    priority: int | None = None
    description: str | None = None
    category: str | None = None
    org_tag: str | None = None
    github_url: str | None = None
    collaborators: str | None = None


@router.get("/projects")
def list_projects(status: str | None = Query(default=None),
                  _: Principal = Depends(_app)):
    if status:
        return {"projects": db.fetchall(
            "SELECT * FROM projects WHERE status = :s ORDER BY last_touched DESC",
            {"s": status})}
    return {"projects": db.fetchall(
        "SELECT * FROM projects ORDER BY last_touched DESC")}


@router.get("/projects/{tag}")
def get_project(tag: str, _: Principal = Depends(_app)):
    row = db.fetchone("SELECT * FROM projects WHERE tag = :t", {"t": tag})
    if not row:
        raise HTTPException(404, f"project not found: {tag}")
    return row


@router.post("/projects")
def create_project(body: ProjectIn, _: Principal = Depends(_app)):
    tag = body.tag or _slug(body.name or "")
    if db.fetchone("SELECT tag FROM projects WHERE tag = :t", {"t": tag}):
        raise HTTPException(409, f"project already exists: {tag}")
    corpus_writes.insert_project({
        "tag": tag, "name": body.name or tag, "status": body.status or "active",
        "priority": body.priority or 3, "description": body.description or "",
        "category": body.category or "", "org_tag": body.org_tag or "",
        "github_url": body.github_url or "", "collaborators": body.collaborators or "",
    })
    return db.fetchone("SELECT * FROM projects WHERE tag = :t", {"t": tag})


@router.patch("/projects/{tag}")
def update_project(tag: str, body: ProjectIn, _: Principal = Depends(_app)):
    if not db.fetchone("SELECT tag FROM projects WHERE tag = :t", {"t": tag}):
        raise HTTPException(404, f"project not found: {tag}")
    fields = body.model_dump(exclude_unset=True, exclude={"tag"})
    if fields:
        corpus_writes.patch_project(tag, fields)
    return db.fetchone("SELECT * FROM projects WHERE tag = :t", {"t": tag})


# ── Organizations (OPT-9 surface tail; read-only) ─────────────────────
# Orgs are curated: rows come from the owner or accepted curator
# proposals, never from generic REST creates, matching the read-only MCP
# org tools. Reads mirror the projects block (app/hub scope only).


@router.get("/organizations")
def list_organizations(_: Principal = Depends(_app)):
    return {"organizations": db.fetchall(
        "SELECT * FROM organizations ORDER BY sort_order, tag")}


@router.get("/organizations/{tag}")
def get_organization(tag: str, _: Principal = Depends(_app)):
    row = db.fetchone("SELECT * FROM organizations WHERE tag = :t",
                      {"t": tag})
    if not row:
        raise HTTPException(404, f"organization not found: {tag}")
    projects = db.fetchall(
        "SELECT tag, name, status FROM projects WHERE org_tag = :t "
        "ORDER BY last_touched DESC", {"t": tag})
    summary = None
    if db.has_table("org_summaries"):
        summary = db.fetchone(
            "SELECT * FROM org_summaries WHERE org_tag = :t", {"t": tag})
    return {**row, "projects": projects, "summary": summary}


# ── Tasks (OPT-3; uuid-idempotent, core-validated) ────────────────────
# Both routes delegate to corpus_writes.upsert_task, which inherits the
# core's write contract (uuid idempotency, status vocabulary, the
# project_tag guard with alias resolution, completed_at stamping). A
# contract rejection from the core (ERR:) maps to 422 so clients can
# distinguish "fix your request" from "server down" (they drop 4xx
# replays and retry 5xx).


class TaskIn(BaseModel):
    uuid: str
    project_tag: str
    title: str
    details: str | None = None
    priority: int | None = None
    due_date: str | None = None


class TaskPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    details: str | None = None
    priority: int | None = None
    due_date: str | None = None
    proposed: int | None = None   # 0 = accept an overseer-proposed task


def _task_write(values: dict):
    try:
        return corpus_writes.upsert_task(values)
    except corpus_writes.CoreWriteError as e:
        if str(e).startswith("ERR:"):
            raise HTTPException(422, str(e)[:200])
        raise


@router.get("/tasks")
def list_tasks(project: str | None = Query(default=None),
               status: str | None = Query(default=None),
               include_proposed: bool = Query(default=False),
               _: Principal = Depends(_app)):
    clauses, params = [], {}
    if project:
        clauses.append("project_tag = :p")
        params["p"] = project
    if status:
        clauses.append("status = :s")
        params["s"] = status
    if not include_proposed:
        clauses.append("proposed = 0")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return {"tasks": db.fetchall(
        f"SELECT * FROM tasks{where} ORDER BY priority, due_date, id",
        params)}


@router.get("/tasks/{task_uuid}")
def get_task(task_uuid: str, _: Principal = Depends(_app)):
    row = db.fetchone("SELECT * FROM tasks WHERE uuid = :u",
                      {"u": task_uuid})
    if not row:
        raise HTTPException(404, f"task not found: {task_uuid}")
    return row


@router.post("/tasks")
def create_task(body: TaskIn, _: Principal = Depends(_app)):
    _task_write({
        "uuid": body.uuid, "project_tag": body.project_tag,
        "title": body.title, "details": body.details or "",
        "priority": body.priority or 3, "due_date": body.due_date or "",
        "source": "mobile", "proposed": 0,
    })
    row = db.fetchone("SELECT * FROM tasks WHERE uuid = :u", {"u": body.uuid})
    if not row:
        raise HTTPException(502, "task write reported success but row absent")
    return row


@router.patch("/tasks/{task_uuid}")
def update_task(task_uuid: str, body: TaskPatch,
                _: Principal = Depends(_app)):
    if not db.fetchone("SELECT uuid FROM tasks WHERE uuid = :u",
                       {"u": task_uuid}):
        raise HTTPException(404, f"task not found: {task_uuid}")
    fields = body.model_dump(exclude_unset=True)
    if fields:
        _task_write({"uuid": task_uuid, **fields})
    return db.fetchone("SELECT * FROM tasks WHERE uuid = :u",
                       {"u": task_uuid})


# ── Notes ─────────────────────────────────────────────────────────────


class NoteIn(BaseModel):
    content: str
    note_type: str | None = "note"
    project: str | None = None
    tags: str | None = None


@router.get("/notes")
def list_notes(project: str | None = Query(default=None),
               limit: int = Query(default=50, le=500),
               _: Principal = Depends(_app)):
    if project:
        return {"notes": db.fetchall(
            "SELECT * FROM notes WHERE project = :p ORDER BY created_at DESC",
            {"p": project})[:limit]}
    return {"notes": db.fetchall(
        "SELECT * FROM notes ORDER BY created_at DESC")[:limit]}


@router.post("/notes")
def create_note(body: NoteIn, _: Principal = Depends(_app)):
    new_id = corpus_writes.insert_note({
        "content": body.content, "note_type": body.note_type or "note",
        "project": body.project or "", "tags": body.tags or "", "source": "cortex",
    })
    return db.fetchone("SELECT * FROM notes WHERE id = :id", {"id": new_id})


# ── People (PK = TEXT id) ─────────────────────────────────────────────


class PersonIn(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    projects: str | None = None
    notes: str | None = None


@router.get("/people")
def list_people(_: Principal = Depends(_app)):
    return {"people": db.fetchall("SELECT * FROM people ORDER BY name")}


@router.post("/people")
def create_person(body: PersonIn, _: Principal = Depends(_app)):
    # First FREE suffix, not count-based: a count drifts after deletes
    # and could mint an id that already exists - and the routed write
    # path upserts, so a collision would silently overwrite that
    # person instead of failing (review finding, 2026-07-20).
    base = pid = _slug(body.name)
    n = 1
    while db.fetchone("SELECT id FROM people WHERE id = :id", {"id": pid}):
        n += 1
        if n > 1000:
            raise HTTPException(409, f"cannot allocate id for: {base}")
        pid = f"{base}-{n}"
    corpus_writes.insert_person({
        "id": pid, "name": body.name, "role": body.role or "",
        "email": body.email or "", "projects": body.projects or "",
        "notes": body.notes or "",
    })
    return db.fetchone("SELECT * FROM people WHERE id = :id", {"id": pid})


# ── Time entries ──────────────────────────────────────────────────────


class TimeIn(BaseModel):
    project_tag: str | None = None
    org_tag: str | None = None
    activity_type: str | None = None
    description: str | None = None
    started_at: str
    duration_minutes: int | None = None


@router.get("/time")
def list_time(limit: int = Query(default=50, le=500), _: Principal = Depends(_app)):
    return {"time_entries": db.fetchall(
        "SELECT * FROM time_entries ORDER BY started_at DESC")[:limit]}


@router.post("/time")
def create_time(body: TimeIn, _: Principal = Depends(_app)):
    new_id = corpus_writes.insert_time_entry({
        "project_tag": body.project_tag or "", "org_tag": body.org_tag or "",
        "activity_type": body.activity_type or "", "description": body.description or "",
        "started_at": body.started_at, "duration_minutes": body.duration_minutes or 0,
        "source": "cortex",
    })
    return db.fetchone("SELECT * FROM time_entries WHERE id = :id", {"id": new_id})
