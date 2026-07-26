"""OPT-4 curator steps (2026-07-26).

The curation reframe (OPT plan section 5): the loop's main function
is keeping the Org -> Project -> Task hierarchy well-formed and its
summaries honest. This module hosts the three OPT-4 steps:

  - fingerprint pass (Step 8.4, zero LLM): the R6 staleness mechanic.
    Recomputes each project's child fingerprint and flags summaries
    whose children moved. COMPARE ONLY; the stamp lives in the shared
    narrative generator (project_narrative.apply_narrative) so manual
    regens never re-flag.
  - task extraction (Step 1c.1): mines proposed=1 task rows out of
    freshly created gists. Hard-capped at a small per-tick call
    reservation so it can never eat import gisting's call slots.
  - structure audit (Step 1b.5): deterministic hierarchy counters
    every tick (zero LLM); a gated LLM half that proposes org
    placements and runs the folded merge check (from the retired
    b_project_merge_check agent) as a mandatory second pass on every
    merge proposal before the Bell fires.

R5 split: the curator's OUTPUTS (task rows) are user data and land in
cortex.db over the core's HTTP upsert contract (the loop passes its
_core_upsert bound method in as `upsert`); the curator's PROCESS
(counters, high-water marks, checked-pair memory) stays in
overseer.db.

Nothing here auto-applies structural changes. Org placements and
merges are Bell proposals via emit_notification; the only rows
written are proposed=1 tasks, and a proposal row is inherently a
proposal.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone


log = logging.getLogger("plugin.overseer.curator")


# overseer_state keys (process memory, overseer.db per R5)
GIST_MARK_KEY = "task_extract_gist_mark"
AUDIT_JSON_KEY = "structure_audit_json"
AUDIT_LLM_LAST_KEY = "structure_audit_llm_last_at"
MERGE_CHECKED_KEY = "merge_checked_json"

MERGEABLE_VERDICTS = ("SAME", "SUBPROJECT_OF_A", "SUBPROJECT_OF_B")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _norm_tag(s: str) -> str:
    """Same normalization as cortex_db's alias seeding: casefold and
    strip separator characters, so 'Open-Muscle' == 'openmuscle'."""
    return (s or "").casefold().replace("-", "").replace("_", "").replace(" ", "")


def _parse_json_array(text: str) -> list:
    """Tolerant JSON-array extraction from an LLM reply. Returns []
    on anything unparseable; conservative is correct here."""
    text = (text or "").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        out = json.loads(text[start:end + 1])
    except ValueError:
        return []
    if not isinstance(out, list):
        return []
    return [t for t in out if isinstance(t, dict)]


# == Fingerprints (R6) ==================================================


def _alias_group(core, project: str) -> list[str]:
    """The canonical tag plus its observed variants from
    project_aliases. Resolved HERE, never caller-supplied, so every
    call site (compare pass, generator stamp, manual routes) hashes
    the same line set."""
    names = {project}
    if core is not None:
        try:
            for r in core.query(
                    "SELECT alias FROM project_aliases WHERE project_tag = ?",
                    (project,)):
                a = (r.get("alias") or "").strip()
                if a:
                    names.add(a)
        except Exception:
            pass
    return sorted(names)


def compute_child_fingerprint(db, project: str, core=None) -> str:
    """sha256 over the project's children: gists (by alias group,
    overseer.db) and task rows (canonical tag, via the read-only core
    handle). Task rows contribute nothing when core is unavailable;
    every stamping call site passes core so stamps and compares stay
    coherent."""
    lines: list[str] = []
    group = _alias_group(core, project)
    marks = ",".join("?" * len(group))
    try:
        rows = db._conn.execute(
            "SELECT id, created_at FROM summaries_gist "
            "WHERE project_tag IN ({})".format(marks), group).fetchall()
        for r in rows:
            lines.append("gist:{}|{}".format(r["id"], r["created_at"] or ""))
    except Exception:
        pass
    if core is not None:
        try:
            for r in core.query(
                    "SELECT uuid, updated_at FROM tasks "
                    "WHERE project_tag = ?", (project,)):
                lines.append("task:{}|{}".format(
                    r.get("uuid") or "", r.get("updated_at") or ""))
        except Exception:
            pass
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def stamp_fingerprint(db, project: str, core=None) -> str:
    """Stamp the freshly-verified child fingerprint on a project's
    summary row and clear its stale flag. Called by the shared
    narrative generator (apply_narrative) so every regen path, loop
    or manual, leaves the queue coherent. Best effort: a missing
    column (pre-migration DB) is a no-op."""
    fp = compute_child_fingerprint(db, project, core=core)
    try:
        db._conn.execute(
            "UPDATE project_summaries SET child_fingerprint = ?, "
            "narrative_stale = 0 WHERE project = ?", (fp, project))
        db._safe_commit()
    except Exception as e:
        log.debug("fingerprint stamp skipped for %s: %s", project, e)
    return fp


def run_fingerprint_pass(*, db, core, summary: dict) -> None:
    """Step 8.4 (zero LLM). Recompute every project's child
    fingerprint and compare against the stored value. A mismatch (or
    a never-stamped row) sets narrative_stale=1. The queue is
    consumed by OPT-6's narrative-gate rewire; until then it
    accumulates and is surfaced in working memory."""
    try:
        rows = db._conn.execute(
            "SELECT project, child_fingerprint, narrative_stale "
            "FROM project_summaries").fetchall()
    except Exception as e:
        summary["errors"].append("fingerprint: " + str(e)[:200])
        return
    # Legacy rows keyed on an OBSERVED variant (pre-OPT-1 summaries
    # like 'Cortex' next to canonical 'cortex') are duplicates the
    # canonical row already covers; flagging them would pollute the
    # queue with rows no generator will ever stamp (review finding,
    # 2026-07-26). The fold/delete cleanup is OPT-5's campaign.
    alias_keys = set()
    if core is not None:
        try:
            alias_keys = {r["alias"] for r in core.query(
                "SELECT alias FROM project_aliases")}
        except Exception:
            alias_keys = set()
    checked = 0
    newly_flagged = 0
    never_stamped = 0
    skipped_alias_rows = 0
    for r in rows:
        project = r["project"]
        if project in alias_keys:
            skipped_alias_rows += 1
            continue
        stored = r["child_fingerprint"] or ""
        checked += 1
        if not stored:
            never_stamped += 1
            stale = True
        else:
            stale = (compute_child_fingerprint(db, project, core=core)
                     != stored)
        if stale and not r["narrative_stale"]:
            db._conn.execute(
                "UPDATE project_summaries SET narrative_stale = 1 "
                "WHERE project = ?", (project,))
            newly_flagged += 1
    db._safe_commit()
    queue_depth = db._conn.execute(
        "SELECT COUNT(*) FROM project_summaries WHERE narrative_stale = 1"
    ).fetchone()[0]
    summary["fingerprint_checked"] = checked
    summary["fingerprint_newly_flagged"] = newly_flagged
    summary["fingerprint_never_stamped"] = never_stamped
    summary["fingerprint_stale_queue"] = int(queue_depth)
    if skipped_alias_rows:
        summary["fingerprint_alias_rows_skipped"] = skipped_alias_rows


# == Task extraction (Step 1c.1) ========================================


TASK_EXTRACT_PROMPT = """\
You review memory gists (short summaries of AI work sessions) and \
extract EXPLICIT open action items as tasks.

Rules:
- Extract a task ONLY when the gist clearly states something still \
needs doing: a named next step, a TODO, a pending decision, an item \
explicitly awaiting someone.
- Work already completed in the session is NOT a task.
- Vague ideas, wishes, or observations are NOT tasks.
- Most gists contain NO task. An empty list is the expected common \
answer.
- At most 2 tasks per gist.

GISTS:
{gists_block}

Reply with ONLY a JSON array (no prose, no code fence):
[{{"gist": <number>, "title": "<imperative, max 80 chars>", \
"details": "<one sentence of context>", "priority": <1-5, optional>}}]
Reply [] if there are none.
"""


def run_task_extraction(*, core, db, llm, cfg, budget, summary: dict,
                        upsert) -> None:
    """Step 1c.1. Batched Flash pass over gists past the high-water
    mark, writing proposed=1 task rows through the core's HTTP upsert
    (which validates project existence, status vocabulary, and uuid
    idempotency). Fixed per-tick reservation of at most
    loop_task_extract_max_calls calls, each batching up to
    loop_task_extract_batch gists; the remainder carries over via the
    mark. On the first ever run the mark anchors to MAX(id) and no
    work happens (the anchor-marks rule; backfill is an explicit
    owner action)."""
    max_calls = int(cfg.get("loop_task_extract_max_calls", 3))
    batch_size = int(cfg.get("loop_task_extract_batch", 10))

    mark = db.get_overseer_state(GIST_MARK_KEY)
    if mark is None:
        row = db._conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM summaries_gist").fetchone()
        anchor = int(row[0])
        db.set_overseer_state(GIST_MARK_KEY, str(anchor))
        summary["task_extract_anchor_set"] = anchor
        log.info("task extraction: anchored gist mark to %s; no work "
                 "this tick", anchor)
        return
    try:
        mark = int(mark)
    except (TypeError, ValueError):
        mark = 0

    window = max(1, max_calls) * max(1, batch_size)
    rows = db._conn.execute(
        "SELECT id, body, project_tag FROM summaries_gist "
        "WHERE id > ? ORDER BY id ASC LIMIT ?", (mark, window)).fetchall()
    if not rows:
        return
    new_mark = int(rows[-1]["id"])
    candidates = [r for r in rows if (r["project_tag"] or "").strip()]
    summary["task_extract_gists_seen"] = len(rows)
    summary["task_extract_gists_eligible"] = len(candidates)

    calls = 0
    written = 0
    skipped_unknown = 0
    failed = 0
    for start in range(0, len(candidates), batch_size):
        if calls >= max_calls or budget.exhausted():
            # Unprocessed candidates carry over: pull the mark back to
            # just before the first unprocessed one. Ineligible gists
            # between here and there get re-scanned next tick, which
            # costs one cheap SQL row each and no LLM calls.
            new_mark = int(candidates[start]["id"]) - 1
            break
        batch = candidates[start:start + batch_size]
        lines = []
        for i, g in enumerate(batch, 1):
            body = (g["body"] or "").strip().replace("\n", " ")[:500]
            lines.append("{}. [project: {}] {}".format(
                i, g["project_tag"], body))
        prompt = TASK_EXTRACT_PROMPT.format(gists_block="\n".join(lines))
        result = llm.complete(prompt, purpose="task-extract",
                              max_tokens=500, temperature=0.2)
        budget.charge(result)
        calls += 1
        if not result.get("ok"):
            # Transport-level failure (the router returns ok=false
            # only after its whole fallback chain is exhausted, and
            # content garbage still parses to []). Pull the mark back
            # so this batch retries next tick instead of silently
            # skipping up to a full window of gists (review finding,
            # 2026-07-26); retry volume is bounded by the per-tick
            # call reservation.
            failed += 1
            new_mark = int(batch[0]["id"]) - 1
            break
        # Insert-only semantics: a proposal is written once; if the
        # uuid already exists (crash between write and mark save, or
        # an owner who already accepted/edited the row), never touch
        # it again (review finding, 2026-07-26).
        existing = set()
        if core is not None:
            try:
                uuids = ["gx{}-{}".format(g["id"], k)
                         for g in batch for k in (1, 2)]
                qmarks = ",".join("?" * len(uuids))
                existing = {r["uuid"] for r in core.query(
                    "SELECT uuid FROM tasks WHERE uuid IN ({})".format(
                        qmarks), tuple(uuids))}
            except Exception:
                existing = set()
        per_gist: dict[int, int] = {}
        for t in _parse_json_array(result.get("text") or ""):
            try:
                n = int(t.get("gist") or 0)
            except (TypeError, ValueError):
                continue
            if not (1 <= n <= len(batch)):
                continue
            title = (t.get("title") or "").strip()[:120]
            if not title:
                continue
            k = per_gist.get(n, 0) + 1
            if k > 2:
                continue
            per_gist[n] = k
            g = batch[n - 1]
            if "gx{}-{}".format(g["id"], k) in existing:
                continue
            data = {
                # Deterministic uuid: a re-run after a crash updates
                # the same row instead of duplicating the proposal.
                "uuid": "gx{}-{}".format(g["id"], k),
                "project_tag": g["project_tag"],
                "title": title,
                "details": (t.get("details") or "").strip()[:500],
                "proposed": 1,
                "source": "overseer-extracted",
                "source_ref": "gist:{}".format(g["id"]),
                "created_by": "overseer",
            }
            try:
                pr = int(t.get("priority") or 0)
                if 1 <= pr <= 5:
                    data["priority"] = pr
            except (TypeError, ValueError):
                pass
            out = upsert("tasks", data)
            if out.get("ok"):
                written += 1
            elif "unknown project" in (out.get("error") or ""):
                # The gist's tag resolves to no known project; the
                # backfill campaign (OPT-5) is the fix, not an
                # auto-created project here.
                skipped_unknown += 1
            else:
                failed += 1

    db.set_overseer_state(GIST_MARK_KEY, str(new_mark))
    summary["task_extract_calls"] = calls
    summary["tasks_extracted"] = written
    if skipped_unknown:
        summary["task_extract_unknown_project"] = skipped_unknown
    if failed:
        summary["task_extract_failed"] = failed


# == Response apply (Step 1b.3, OPT-5 accept path) ======================


CURATOR_RULES = ("curator_org_proposal", "curator_merge_proposal")


def run_apply_responses(*, db, summary: dict, upsert, core_cmd) -> None:
    """OPT-5: apply the owner's Bell decisions on curator proposals.

    Reads unprocessed notification_responses for the curator rules
    and applies accepts through the core's public write contract:
      - org_assign: partial projects update setting org_tag.
      - merge_apply: CMD merge_project with an EXPLICIT winner/loser
        in the payload. A merge_review click alone never merges;
        direction must be stated, per the propose-then-accept terms.
      - reject / merge_review / anything else: acknowledged, no write.

    Only curator-rule responses are marked processed here; responses
    for every other rule stay queued for the journal step, their
    existing consumer. Failed applies are still dequeued (the click
    was seen; retrying a bad payload every tick would wedge the
    queue) and surfaced in the tick summary."""
    try:
        pending = db.list_pending_notification_responses(limit=50)
    except Exception as e:
        summary["errors"].append("apply_responses: " + str(e)[:200])
        return
    handled = []
    applied_orgs = 0
    applied_merges = 0
    acknowledged = 0
    failed = 0
    for r in pending:
        if r.get("rule_name") not in CURATOR_RULES:
            continue
        kind = (r.get("action_kind") or "").strip()
        payload = r.get("response_payload") or {}
        ok = True
        if kind == "org_assign":
            ptag = (payload.get("project_tag") or "").strip()
            org = (payload.get("org_tag") or "").strip()
            if ptag and org:
                out = upsert("projects", {"tag": ptag, "org_tag": org})
                ok = bool(out.get("ok"))
                if ok:
                    applied_orgs += 1
            else:
                ok = False
        elif kind == "merge_apply":
            loser = (payload.get("loser") or "").strip()
            winner = (payload.get("winner") or "").strip()
            if loser and winner:
                out = core_cmd("merge_project",
                               {"loser": loser, "winner": winner})
                ok = bool(out.get("ok"))
                if ok:
                    applied_merges += 1
            else:
                ok = False
        else:
            acknowledged += 1
        if not ok:
            failed += 1
        handled.append(r["id"])
    if handled:
        try:
            db.mark_notification_responses_processed(
                response_ids=handled)
        except Exception as e:
            summary["errors"].append(
                "apply_responses_mark: " + str(e)[:200])
        summary["curator_responses_applied"] = {
            "handled": len(handled), "org_assigns": applied_orgs,
            "merges": applied_merges, "acknowledged": acknowledged,
            "failed": failed}


# == Structure audit (Step 1b.5) ========================================


ORG_CLASSIFY_PROMPT = """\
You triage the owner's project list into their organizations.

ORGANIZATIONS (tag: name; type; notes):
{orgs_block}

UNCLASSIFIED PROJECTS (tag: name; category; description):
{projects_block}

For each project pick the single best organization, or "unsorted" \
when nothing clearly fits. Be conservative: prefer "unsorted" over a \
weak guess.

Reply with ONLY a JSON array (no prose, no code fence):
[{{"project": "<tag>", "org": "<org tag>", "confidence": \
"high|med|low", "why": "<one short sentence>"}}]
"""


# Folded from the retired b_project_merge_check agent (b_agents.py).
# The signal guidance is battle-tested; keep it intact.
MERGE_CHECK_PROMPT = """\
Two project tags are candidates for merging. Independently assess: \
are they the SAME work, is one a SUBPROJECT of the other, or are \
they DISTINCT? This is an audit layer that runs BEFORE a merge \
proposal reaches the owner; its job is reducing false-positive \
merge proposals.

Signals that argue SAME:
- Identical or near-identical names
- Heavy overlap in top files (same code paths, same repos)
- Overlapping active periods AND no semantic differentiator

Signals that argue SUBPROJECT_OF_A (or SUBPROJECT_OF_B):
- One is clearly narrower scope
- Description text describes a component of the other
- Shared or nested github_url
- Parent's first activity predates the subproject's

DO NOT use session count or recency as a parent-direction signal. \
Session count is a RECENCY ARTIFACT; it tells you what has been \
worked on lately, not which project contains the other. If parent \
direction is ambiguous after the structural signals, prefer \
INSUFFICIENT_DATA over guessing; a wrong SUBPROJECT_OF verdict can \
drive a merge in the wrong direction.

Signals that argue DISTINCT:
- Different categories
- Different github_urls (different repos)
- Non-overlapping active periods + non-overlapping top files
- Names/descriptions pointing at different domains

TAG A:
{block_a}

TAG B:
{block_b}

Reply in EXACTLY this format:
VERDICT: <SAME|SUBPROJECT_OF_A|SUBPROJECT_OF_B|DISTINCT|INSUFFICIENT_DATA>
<one paragraph (3-6 sentences) citing the specific signals that \
drove the verdict, naming fields and values. If INSUFFICIENT_DATA, \
say what is missing.>
"""


def _deterministic_audit(core, db) -> dict:
    """The zero-LLM half: hierarchy health counters. Every counter is
    independently best-effort; a missing table or column reports null
    rather than failing the step."""
    audit: dict = {"computed_at": _utc_iso()}

    def _count(sql, params=()):
        rows = core.query(sql, params) if params else core.query(sql)
        return int(rows[0]["n"]) if rows else 0

    try:
        audit["untriaged_projects"] = _count(
            "SELECT COUNT(*) AS n FROM projects "
            "WHERE COALESCE(org_tag, '') = ''")
    except Exception:
        audit["untriaged_projects"] = None

    try:
        groups: dict[str, list[str]] = {}
        for r in core.query("SELECT tag FROM projects"):
            groups.setdefault(_norm_tag(r["tag"]), []).append(r["tag"])
        audit["tag_collision_groups"] = sorted(
            sorted(v) for v in groups.values() if len(v) > 1)
    except Exception:
        audit["tag_collision_groups"] = []

    try:
        audit["stale_active_projects_90d"] = _count(
            "SELECT COUNT(*) AS n FROM projects WHERE status = 'active' "
            "AND last_touched < datetime('now', '-90 days')")
    except Exception:
        audit["stale_active_projects_90d"] = None

    try:
        observed = {
            r["project"] for r in db._conn.execute(
                "SELECT DISTINCT project FROM imported_sessions "
                "WHERE project != ''").fetchall()}
        known = {r["tag"] for r in core.query("SELECT tag FROM projects")}
        aliases = {r["alias"] for r in core.query(
            "SELECT alias FROM project_aliases")}
        unmapped = sorted(observed - known - aliases)
        audit["unmapped_imported_names"] = len(unmapped)
        audit["unmapped_imported_sample"] = unmapped[:10]
    except Exception:
        audit["unmapped_imported_names"] = None

    try:
        audit["time_entry_org_disagreements"] = _count(
            "SELECT COUNT(*) AS n FROM time_entries t "
            "JOIN projects p ON p.tag = t.project_tag "
            "WHERE COALESCE(t.org_tag, '') != '' "
            "AND t.org_tag != COALESCE(p.org_tag, '')")
    except Exception:
        audit["time_entry_org_disagreements"] = None

    return audit


def _proposed_keys(db, rule_name: str) -> set:
    """ALL rule_keys ever emitted for this rule, including dismissed
    and archived rows. The live emit_notification is a plain INSERT
    against UNIQUE(rule_name, rule_key), so a re-emit of an acted-on
    proposal raises instead of coalescing; and re-classifying a
    rejected project every gated run would waste the daily Flash call
    while never re-Belling (review finding, 2026-07-26). A project or
    pair is therefore proposed AT MOST ONCE; explicit re-propose
    machinery is OPT-5's Bell campaign."""
    try:
        rows = db._conn.execute(
            "SELECT rule_key FROM notifications WHERE rule_name = ?",
            (rule_name,)).fetchall()
        return {r["rule_key"] for r in rows}
    except Exception:
        return set()


def _hours_since(iso_ts: str) -> float:
    try:
        then = datetime.fromisoformat(iso_ts.replace(" ", "T"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
    except Exception:
        return 1e9


def _merge_pair_block(core, db, tag: str) -> str:
    """Lean snapshot of one tag for the merge check: projects row,
    summary stats, and up to 3 recent gist bodies."""
    parts = []
    try:
        rows = core.query(
            "SELECT name, status, category, org_tag, github_url, "
            "description, created_at, last_touched FROM projects "
            "WHERE tag = ?", (tag,))
        parts.append("projects row: " + (
            json.dumps(rows[0], default=str) if rows else "(none)"))
    except Exception:
        parts.append("projects row: (unavailable)")
    try:
        row = db.get_project_summary(tag)
    except Exception:
        row = None
    if row:
        parts.append(
            "summary: sessions={} active_min={} first={} last={}".format(
                row.get("session_count"), row.get("active_minutes_total"),
                (row.get("first_active_at") or "")[:10],
                (row.get("last_active_at") or "")[:10]))
        top = (row.get("top_files_json") or "[]")[:300]
        parts.append("top_files: " + top)
        narr = (row.get("narrative") or "").strip().replace("\n", " ")
        if narr:
            parts.append("narrative excerpt: " + narr[:300])
    else:
        parts.append("summary: (none)")
    try:
        for g in db.gists_for_project(project=tag, limit=3):
            body = (g.get("body") or "").strip().replace("\n", " ")
            parts.append("gist g:{}: {}".format(g.get("id"), body[:200]))
    except Exception:
        pass
    return "\n".join(parts)


def run_structure_audit(*, core, db, llm, cfg, budget,
                        summary: dict) -> None:
    """Step 1b.5. Deterministic counters every tick; the LLM half
    copies the distill-corrections gate (enough pending items AND
    enough hours since the last run) and proposes org placements plus
    merge candidates, everything as Bell proposals. The folded merge
    check runs as a mandatory second pass BEFORE any merge Bell
    fires."""
    audit = _deterministic_audit(core, db)
    try:
        db.set_overseer_state(AUDIT_JSON_KEY, json.dumps(audit))
    except Exception:
        pass
    summary["structure_audit"] = {
        k: v for k, v in audit.items()
        if k not in ("unmapped_imported_sample",)}

    if budget.exhausted():
        return

    min_pending = int(cfg.get("loop_audit_min_pending", 3))
    interval_hours = int(cfg.get("loop_audit_interval_hours", 24))
    classify_cap = int(cfg.get("loop_audit_classify_per_tick", 10))
    merge_cap = int(cfg.get("loop_audit_merge_checks_per_tick", 2))
    max_cost = float(cfg.get("loop_audit_max_cost_usd", 0.05))

    # Pending work = untriaged projects never proposed + collision
    # pairs never merge-checked.
    open_props = _proposed_keys(db, "curator_org_proposal")
    try:
        untriaged = core.query(
            "SELECT tag, name, COALESCE(description, '') AS description, "
            "category, status FROM projects "
            "WHERE COALESCE(org_tag, '') = '' "
            "ORDER BY last_touched DESC LIMIT 50")
    except Exception:
        untriaged = []
    candidates = [r for r in untriaged
                  if ("org-" + r["tag"]) not in open_props]

    try:
        checked = json.loads(
            db.get_overseer_state(MERGE_CHECKED_KEY) or "{}")
    except Exception:
        checked = {}
    pairs = []
    for grp in audit.get("tag_collision_groups") or []:
        for i in range(len(grp) - 1):
            a, b = grp[i], grp[i + 1]
            if "{}|{}".format(a, b) not in checked:
                pairs.append((a, b))

    pending_total = len(candidates) + len(pairs)
    summary["structure_audit"]["llm_pending"] = pending_total
    if pending_total < min_pending:
        return
    last = db.get_overseer_state(AUDIT_LLM_LAST_KEY)
    if last and _hours_since(last) < interval_hours:
        return

    audit_cost = 0.0
    calls = 0

    # -- Org placement proposals (one batched Flash call) --
    proposals_emitted = 0
    if candidates and not budget.exhausted():
        try:
            orgs = core.query(
                "SELECT tag, name, COALESCE(org_type, '') AS org_type, "
                "COALESCE(notes, '') AS notes, "
                "COALESCE(is_default, 0) AS is_default "
                "FROM organizations WHERE is_active = 1 ORDER BY tag")
        except Exception:
            orgs = []
        # The default bucket ('unsorted') stays a valid ANSWER in the
        # prompt but never becomes a Bell: assigning a project to the
        # catch-all is a no-op decision and would be pure noise. When
        # NO non-default org exists yet (the fresh-install state) the
        # classify call is guaranteed to produce zero proposals, so
        # skip it entirely rather than spend the gated call.
        valid_orgs = {o["tag"] for o in orgs if not o["is_default"]}
        if not valid_orgs:
            summary["structure_audit"]["org_classify_skipped"] = (
                "no non-default orgs")
        if valid_orgs:
            orgs_block = "\n".join(
                "- {}: {}; {}; {}".format(
                    o["tag"], o["name"], o["org_type"],
                    (o["notes"] or "")[:120])
                for o in orgs)
            batch = candidates[:classify_cap]
            projects_block = "\n".join(
                "- {}: {}; {}; {}".format(
                    r["tag"], r["name"], r["category"] or "",
                    (r["description"] or "")[:160])
                for r in batch)
            prompt = ORG_CLASSIFY_PROMPT.format(
                orgs_block=orgs_block, projects_block=projects_block)
            result = llm.complete(prompt, purpose="org-classify",
                                  max_tokens=700, temperature=0.2)
            budget.charge(result)
            calls += 1
            audit_cost += float(result.get("cost_usd") or 0.0)
            if result.get("ok"):
                batch_tags = {r["tag"] for r in batch}
                for p in _parse_json_array(result.get("text") or ""):
                    ptag = (p.get("project") or "").strip()
                    org = (p.get("org") or "").strip()
                    if ptag not in batch_tags or org not in valid_orgs:
                        continue
                    conf = (p.get("confidence") or "low").strip()
                    why = (p.get("why") or "").strip()[:300]
                    try:
                        db.emit_notification(
                            severity="info",
                            title="Org proposal: {} -> {}".format(
                                ptag, org),
                            body="{} confidence. {}".format(conf, why),
                            rule_name="curator_org_proposal",
                            rule_key="org-" + ptag,
                            related_table="projects",
                            related_id=ptag,
                            actions=[
                                {"label": "Assign to " + org,
                                 "kind": "org_assign",
                                 "payload": {"project_tag": ptag,
                                             "org_tag": org}},
                                {"label": "Not this org",
                                 "kind": "reject"},
                            ],
                        )
                        proposals_emitted += 1
                    except Exception:
                        pass  # UNIQUE(rule_name, rule_key) coalesces

    # -- Merge checks (mandatory second pass before any merge Bell) --
    merge_bells = 0
    merge_rejected = 0
    for a, b in pairs[:merge_cap]:
        if budget.exhausted() or audit_cost >= max_cost:
            break
        prompt = MERGE_CHECK_PROMPT.format(
            block_a=_merge_pair_block(core, db, a),
            block_b=_merge_pair_block(core, db, b))
        result = llm.complete(prompt, purpose="merge-check",
                              max_tokens=600, temperature=0.2)
        budget.charge(result)
        calls += 1
        audit_cost += float(result.get("cost_usd") or 0.0)
        if not result.get("ok"):
            continue
        text = (result.get("text") or "").strip()
        verdict = ""
        body = text
        if text.upper().startswith("VERDICT:"):
            first, _, rest = text.partition("\n")
            verdict = first.split(":", 1)[1].strip().upper()
            body = rest.strip()
        if not verdict:
            # Formatting hiccup: treat like a failed call so the pair
            # stays eligible next run instead of being consumed
            # forever (review finding, 2026-07-26).
            summary["structure_audit"]["merge_checks_unparsed"] = (
                summary["structure_audit"].get(
                    "merge_checks_unparsed", 0) + 1)
            continue
        checked["{}|{}".format(a, b)] = {
            "verdict": verdict, "at": _utc_iso()}
        if verdict in MERGEABLE_VERDICTS:
            try:
                db.emit_notification(
                    severity="warn",
                    title="Merge proposal: {} + {}".format(a, b),
                    body="Independent check verdict: {}\n\n{}".format(
                        verdict, body[:800]),
                    rule_name="curator_merge_proposal",
                    rule_key="merge-{}-{}".format(a, b),
                    related_table="projects",
                    related_id=a,
                    actions=[
                        {"label": "Review merge", "kind": "merge_review",
                         "payload": {"tag_a": a, "tag_b": b,
                                     "verdict": verdict}},
                        {"label": "Keep separate", "kind": "reject"},
                    ],
                )
                merge_bells += 1
            except Exception:
                pass
        else:
            merge_rejected += 1

    if pairs[:merge_cap]:
        try:
            db.set_overseer_state(MERGE_CHECKED_KEY, json.dumps(checked))
        except Exception:
            pass
    if calls:
        db.set_overseer_state(AUDIT_LLM_LAST_KEY, _utc_iso())
    summary["structure_audit"]["llm_calls"] = calls
    summary["structure_audit"]["llm_cost_usd"] = round(audit_cost, 4)
    summary["structure_audit"]["org_proposals"] = proposals_emitted
    summary["structure_audit"]["merge_proposals"] = merge_bells
    if merge_rejected:
        summary["structure_audit"]["merge_checks_rejected"] = merge_rejected
