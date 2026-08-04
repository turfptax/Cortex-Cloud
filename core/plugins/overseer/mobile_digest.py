"""Notes digest: fold each day of the owner's captures into the pipeline.

Step 1c.7. Sessionless notes never reach the session-driven gist step, so
without this they are tagged and raw-searchable but never gisted, embedded,
or routed against open questions. Per the locked pipeline vision
(2026-06-11), the owner's own captures are the HIGHEST-value content in the
corpus; this step gives them the full treatment.

WHY THIS STOPPED BEING "MOBILE" (2026-08-04): the original step filtered on
`source = 'mobile'`, which was the phone sync value. Every OTHER capture
route writes a different value: the wearable and desktop write 'ble', the
voice journal 'voice', the web Hub 'hub', gateway ingest 'cortex' or
'ai-generated'. All of them were silently excluded. On a June snapshot there
were zero 'mobile' rows at all, so the one step that gleans the owner's
words was digesting nothing while looking healthy. Worse, the documented
source vocabulary never listed 'mobile', so the filter looked correct
against the docs.

The filter is now a DENYLIST of bulk archive sources, so a new capture
route is included by default and a fat import has to be named to be
excluded. That is the safe direction to be wrong in: a missed archive costs
noise, a missed capture costs the owner's own voice.

Behavior:
  - Gathers sessionless notes from cortex.db, minus the excluded sources.
  - Groups by COMPLETE local day (today is skipped so the digest always
    sees the whole day; local_created_at preferred, slice 9.4.1).
  - One gist per day, period_label 'user-notes:<YYYY-MM-DD>', via the
    standard summarize-session model tier and THE CHANGE framing.
  - Routes the gist against active open questions (question_routing).
  - Embeddings arrive via the existing missing-embeddings backfill.
  - High-water mark in overseer_state ('notes_digest_done_through').
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from prompts import mobile_digest_prompt, notes_digest_prompt
from question_routing import route_evidence_to_questions

STATE_KEY = "notes_digest_done_through"
LEGACY_STATE_KEY = "mobile_digest_done_through"
MAX_BODY_CHARS = 8000
MAX_DAYS_PER_RUN = 7
SEED_DAYS = 7

# Bulk imports, not captures. These stay searchable (Slice A) but are not
# worth a per-day LLM digest on the tick loop: the twitter archive alone is
# ~1,500 notes spread over hundreds of past days. Backfilling them is a
# separate, owner-approved operation.
DEFAULT_EXCLUDED_SOURCES = ("twitter", "google-takeout", "tory_life.db")


def _local_day(row: dict) -> str:
    return ((row.get("local_created_at") or row.get("created_at") or "")[:10])


def _fetch_notes(core, excluded):
    """Sessionless capture notes, oldest first.

    Falls back to a created_at-only SELECT when local_created_at is absent.
    That column is added at runtime by timestamp_localizer rather than being
    in the DDL, so a database that has not been through that path (a fresh
    deploy, an old backup, a test fixture) lacks it.
    """
    placeholders = ",".join("?" for _ in excluded) or "''"
    where = ("WHERE (session_id IS NULL OR session_id = '') "
             "AND COALESCE(source, '') NOT IN ({})".format(placeholders))
    try:
        return core.query(
            "SELECT id, content, note_type, project, source, created_at, "
            "local_created_at FROM notes " + where + " ORDER BY created_at",
            tuple(excluded))
    except sqlite3.OperationalError:
        return core.query(
            "SELECT id, content, note_type, project, source, created_at "
            "FROM notes " + where + " ORDER BY created_at", tuple(excluded))


def run_mobile_digest(*, core, db, llm, budget=None, log=None,
                      summary: dict | None = None) -> dict:
    """Legacy entry point: phone captures only, legacy state key.

    Kept so `loop_mobile_digest_enabled` remains a working escape hatch if
    the generalized step has to be switched off in a hurry.
    """
    return run_notes_digest(core=core, db=db, llm=llm, budget=budget, log=log,
                            summary=summary, cfg={},
                            _sources_mode="mobile-only")


def run_notes_digest(*, core, db, llm, cfg=None, budget=None, log=None,
                     summary: dict | None = None,
                     _sources_mode: str = "captures") -> dict:
    """core: CoreMemoryRO (cortex.db, read-only). db: OverseerDB.
    Returns {ok, days_digested, gist_ids, skipped_reason?}."""
    cfg = cfg or {}
    out = {"ok": True, "days_digested": 0, "gist_ids": []}

    legacy = _sources_mode == "mobile-only"
    state_key = LEGACY_STATE_KEY if legacy else STATE_KEY
    label_prefix = "mobile-notes" if legacy else "user-notes"
    max_days = int(cfg.get("loop_notes_digest_max_days_per_run",
                           MAX_DAYS_PER_RUN))

    if legacy:
        rows = core.query(
            "SELECT id, content, note_type, project, source, created_at, "
            "local_created_at FROM notes WHERE source = 'mobile' "
            "AND (session_id IS NULL OR session_id = '') ORDER BY created_at")
    else:
        excluded = tuple(cfg.get("loop_notes_digest_exclude_sources",
                                 DEFAULT_EXCLUDED_SOURCES))
        rows = _fetch_notes(core, excluded)
    if not rows:
        return out

    # Owner's calendar day, not the container's: in a UTC container
    # host-today outruns owner-today from evening on, which would let
    # the IN-PROGRESS day pass the d < today_local guard and get
    # digested half-finished (tenant-TZ pass, cloud P2 2026-07-20).
    from temporal import tenant_tz
    _tz = tenant_tz()
    today_local = ((datetime.now(_tz) if _tz is not None
                    else datetime.now().astimezone())
                   .strftime("%Y-%m-%d"))
    done_through = str(db.get_overseer_state(state_key, "") or "")
    if not done_through and not legacy:
        # First run of the generalized step. Do NOT eat every historical day
        # the moment this ships: the corpus reaches back years, and chewing
        # through all of it on one tick would blow the budget and bury the
        # recent past under a wall of old gists. Start SEED_DAYS back, and
        # never behind what the phone-only step already covered so those
        # days are not digested twice.
        seed_days = int(cfg.get("loop_notes_digest_seed_days", SEED_DAYS))
        seed = (datetime.strptime(today_local, "%Y-%m-%d")
                - timedelta(days=seed_days + 1)).strftime("%Y-%m-%d")
        legacy_mark = str(db.get_overseer_state(LEGACY_STATE_KEY, "") or "")
        done_through = max(seed, legacy_mark)
        if log:
            log.info("notes digest seeding high-water mark at %s",
                     done_through)
    days = sorted({d for d in (_local_day(r) for r in rows) if d})
    todo = [d for d in days if d > done_through and d < today_local]
    todo = todo[:max_days]

    for day in todo:
        if budget is not None and budget.exhausted():
            out["skipped_reason"] = "budget exhausted"
            break
        notes = [r for r in rows if _local_day(r) == day]
        lines = []
        for r in notes:
            ts = (r.get("local_created_at") or r.get("created_at") or "")[11:16]
            kind = r.get("note_type") or "note"
            src = r.get("source") or ""
            proj = (" proj:" + r["project"]) if r.get("project") else ""
            lines.append("[{}] ({}/{}{}) {}".format(
                ts, kind, src or "?", proj, (r.get("content") or "").strip()))
        body = "\n".join(lines)[:MAX_BODY_CHARS]

        sources_present = sorted({(r.get("source") or "?") for r in notes})
        prompt = (mobile_digest_prompt(day=day, n_notes=len(notes), body=body)
                  if legacy else
                  notes_digest_prompt(day=day, n_notes=len(notes), body=body,
                                      sources=sources_present))
        result = llm.complete(prompt, max_tokens=200, temperature=0.4,
                              purpose="summarize-session")
        if budget is not None:
            budget.charge(result)
        if not result.get("ok"):
            if log:
                log.warning("notes digest %s failed: %s",
                            day, result.get("error"))
            out["ok"] = False
            break
        gist_text = (result.get("text") or "").strip().strip('"').strip()
        if not gist_text:
            # Empty reply: advance the mark anyway so one bad day cannot
            # wedge the queue forever; the raw notes remain searchable.
            db.set_overseer_state(state_key, day)
            continue

        gid = db.add_gist(
            gist_text,
            period_label="{}:{}".format(label_prefix, day),
            period_start="{} 00:00:00".format(day),
            period_end="{} 23:59:59".format(day),
            confidence="med",
            tags=(["auto", "mobile-digest", "source:mobile"] if legacy
                  else ["auto", "notes-digest"]
                  + ["src:" + s for s in sources_present]),
        )
        out["gist_ids"].append(gid)
        out["days_digested"] += 1
        db.set_overseer_state(state_key, day)

        try:
            route_evidence_to_questions(
                db=db, llm=llm, gist_text=gist_text, gist_id=gid,
                budget=budget,
                contributed_by=("auto:mobile-digest" if legacy
                                else "auto:notes-digest"))
        except Exception as e:
            if log:
                log.warning("notes digest routing %s failed: %s", day, e)

        if summary is not None:
            summary.setdefault(
                "mobile_digests" if legacy else "notes_digests", []).append(
                {"day": day, "gist_id": gid, "notes": len(notes),
                 "sources": sources_present})
            # Counter the journal watches, so a tick that only digested
            # notes still counts as notable work worth writing about.
            summary["note_days_digested"] = (
                summary.get("note_days_digested", 0) + 1)

    return out
