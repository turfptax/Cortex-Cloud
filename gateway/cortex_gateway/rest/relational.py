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
