"""OPT-6: the org-level abstraction (Step 8.5).

Per the three-layer rule, the org narrative composes STRICTLY from
member project narratives, never raw. Per R5 the rollup rows are user
data: they live in cortex.db (org_summaries) and every write goes
over the core's HTTP upsert contract (the loop passes its
_core_upsert bound method in).

R6 staleness, same mechanic as projects: the org's child fingerprint
is sha256 over its membership plus each member's narrative freshness.
The stats pass recomputes and compares every tick (free); a mismatch
flags narrative_stale. The narrative half regenerates only for
stale-flagged orgs at least 24h after their last narrative, capped
per tick and per call, and stamps narrative + fingerprint + prompt
version atomically in one upsert, so tick output and manual cold
starts are indistinguishable to the queue.

The default bucket ('unsorted') gets stats but never a narrative:
narrating the catch-all would be noise.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone


log = logging.getLogger("plugin.overseer.org_rollup")


DEFAULT_MAX_PER_TICK = 2
DEFAULT_MAX_COST_USD = 0.05
DEFAULT_MIN_HOURS_BETWEEN = 24


ORG_NARRATIVE_PROMPT = """\
You are Cortex writing the organization-level rollup for ONE of the \
owner's organizations. The owner is direct and intellectually \
serious; accurate observation beats flattery.

ORGANIZATION: {org_name} ({org_tag}), type: {org_type}
{org_notes_line}
DETERMINISTIC STATS (the UI shows these; do not repeat them):
{stats_block}

MEMBER PROJECT NARRATIVES (the org story is synthesized from these; \
cite project tags when referencing specifics):
{members_block}

FORMAT YOUR REPLY AS:

Paragraph 1 - WHAT this organization is and does, synthesized \
across its members. Two-three sentences, specific.

Paragraph 2 - CURRENT ACTIVITY: where the energy is now, which \
members are moving, which are quiet.

Paragraph 3 - PATTERNS OR RISKS worth flagging at the org level: \
drift, concentration, stalls, or momentum that no single project \
narrative can see.

CONSTRAINTS:
- Under 300 words total. No lists, no headers.
- No hedging openers. State observations directly.
- Members without narratives yet: note the gap once if it matters, \
do not apologize for it.
- PRESERVE any `[B:<name>]` or `[C:<name>]` markers verbatim if the \
inputs carry them; they are authorship attribution.
"""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _hours_since(iso_ts: str) -> float:
    try:
        then = datetime.fromisoformat((iso_ts or "").replace(" ", "T"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
    except Exception:
        return 1e9


def _members(core, org_tag: str) -> list[dict]:
    try:
        return core.query(
            "SELECT tag, name, status, total_hours, last_touched "
            "FROM projects WHERE org_tag = ? ORDER BY tag", (org_tag,))
    except Exception:
        return []


def compute_org_fingerprint(db, core, org_tag: str) -> str:
    """sha256 over the org's children: the member set plus each
    member's project-narrative freshness. Membership changes, member
    adds/removes, and any member narrative regen all move the hash;
    quiet orgs never do. Deterministic across compare and stamp
    because both call HERE."""
    lines = []
    for m in _members(core, org_tag):
        tag = m.get("tag") or ""
        narr_at = ""
        try:
            row = db.get_project_summary(tag)
            if row:
                narr_at = row.get("narrative_updated_at") or ""
        except Exception:
            pass
        lines.append("proj:{}|{}".format(tag, narr_at))
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _stats_for(core, org_tag: str, members: list[dict]) -> dict:
    active = [m for m in members if (m.get("status") or "") == "active"]
    hours = sum(float(m.get("total_hours") or 0) for m in members)
    last = max((m.get("last_touched") or "" for m in members),
               default="")
    open_tasks = 0
    try:
        rows = core.query(
            "SELECT COUNT(*) AS n FROM tasks t "
            "JOIN projects p ON p.tag = t.project_tag "
            "WHERE p.org_tag = ? AND t.proposed = 0 "
            "AND t.status IN ('open', 'in_progress', 'blocked')",
            (org_tag,))
        open_tasks = int(rows[0]["n"]) if rows else 0
    except Exception:
        pass
    return {
        "member_count": len(members),
        "active_count": len(active),
        "total_hours": round(hours, 2),
        "last_active_at": last,
        "open_tasks": open_tasks,
    }


def _existing_rows(core) -> dict:
    try:
        return {r["org_tag"]: r for r in core.query(
            "SELECT * FROM org_summaries")}
    except Exception:
        return {}


def run_org_rollup(*, core, db, llm, cfg, budget, summary: dict,
                   upsert, force_narratives: bool = False,
                   max_narratives=None) -> None:
    """Step 8.5. Deterministic stats + fingerprint compare for every
    org each call (free); gated narrative regen for stale orgs.
    force_narratives ignores the 24h gate (cold start / manual
    route); the per-call cost cap always holds."""
    max_per = int(max_narratives if max_narratives is not None
                  else cfg.get("loop_org_narrative_max_per_tick",
                               DEFAULT_MAX_PER_TICK))
    max_cost = float(cfg.get("loop_org_narrative_max_cost",
                             DEFAULT_MAX_COST_USD))
    min_hours = int(cfg.get("loop_org_narrative_min_hours",
                            DEFAULT_MIN_HOURS_BETWEEN))

    try:
        orgs = core.query(
            "SELECT tag, name, COALESCE(org_type, '') AS org_type, "
            "COALESCE(notes, '') AS notes, "
            "COALESCE(is_default, 0) AS is_default "
            "FROM organizations WHERE is_active = 1 ORDER BY tag")
    except Exception as e:
        summary["errors"].append("org_rollup: " + str(e)[:200])
        return
    existing = _existing_rows(core)

    stats_written = 0
    newly_stale = 0
    candidates = []
    for o in orgs:
        tag = o["tag"]
        members = _members(core, tag)
        stats = _stats_for(core, tag, members)
        fp = compute_org_fingerprint(db, core, tag)
        prev = existing.get(tag) or {}
        stored_fp = prev.get("child_fingerprint") or ""
        stale = int(prev.get("narrative_stale") or 0)
        if not o["is_default"]:
            if not stored_fp or fp != stored_fp:
                if not stale:
                    newly_stale += 1
                stale = 1
        data = {"org_tag": tag, "narrative_stale": stale, **stats}
        out = upsert("org_summaries", data)
        if out.get("ok"):
            stats_written += 1
        if (not o["is_default"]) and stale:
            candidates.append((o, members, stats, fp, prev))

    summary["org_rollup_stats_written"] = stats_written
    summary["org_rollup_newly_stale"] = newly_stale
    summary["org_rollup_stale_queue"] = len(candidates)

    generated = 0
    gen_cost = 0.0
    for o, members, stats, fp, prev in candidates:
        if generated >= max_per or budget.exhausted():
            break
        narr_at = prev.get("narrative_updated_at") or ""
        if (not force_narratives and narr_at
                and _hours_since(narr_at) < min_hours):
            continue

        members_block = []
        for m in members:
            narr = ""
            try:
                row = db.get_project_summary(m["tag"])
                narr = ((row or {}).get("narrative") or "").strip()
            except Exception:
                pass
            excerpt = narr.replace("\n", " ")[:600] if narr else \
                "(no narrative yet)"
            members_block.append(
                "- {} [{}], {}h: {}".format(
                    m["tag"], m.get("status") or "?",
                    round(float(m.get("total_hours") or 0), 1),
                    excerpt))
        stats_block = ("- {member_count} member projects, "
                       "{active_count} active\n"
                       "- {total_hours} tracked hours\n"
                       "- last activity {last_active_at}\n"
                       "- {open_tasks} open tasks").format(**stats)
        notes = (o.get("notes") or "").strip()
        template, pvid = db.resolve_prompt(
            "org-narrative", ORG_NARRATIVE_PROMPT)
        fmt = dict(org_name=o.get("name") or o["tag"],
                   org_tag=o["tag"],
                   org_type=o.get("org_type") or "?",
                   org_notes_line=("Owner notes: " + notes + "\n")
                   if notes else "",
                   stats_block=stats_block,
                   members_block="\n".join(members_block)
                   or "(no member projects)")
        try:
            prompt = template.format(**fmt)
        except Exception as e:
            log.warning("org-narrative v%s broken placeholders (%s); "
                        "using constant", pvid, e)
            prompt = ORG_NARRATIVE_PROMPT.format(**fmt)
            pvid = 0

        result = llm.complete(prompt, purpose="org-narrative",
                              max_tokens=700, temperature=0.5)
        budget.charge(result)
        cost = float(result.get("cost_usd") or 0.0)
        gen_cost += cost
        if cost > max_cost:
            log.warning("org narrative for %s cost $%.4f over the "
                        "$%.2f cap", o["tag"], cost, max_cost)
        if not result.get("ok"):
            summary.setdefault("org_narratives_failed", 0)
            summary["org_narratives_failed"] += 1
            continue
        text = (result.get("text") or "").strip()
        if not text:
            continue
        # Atomic stamp: narrative + fingerprint + version together,
        # clearing the stale flag, so the queue stays coherent.
        out = upsert("org_summaries", {
            "org_tag": o["tag"],
            "narrative": text,
            "narrative_updated_at": _utc_iso(),
            "narrative_cost_usd": round(cost, 4),
            "narrative_prompt_version_id": int(pvid or 0),
            "child_fingerprint": fp,
            "narrative_stale": 0,
        })
        if out.get("ok"):
            generated += 1

    if generated or gen_cost:
        summary["org_narratives_generated"] = generated
        summary["org_narratives_cost_usd"] = round(gen_cost, 4)
