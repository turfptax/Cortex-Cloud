"""OPT-7: recall-graded quality (Step 9, zero LLM).

One SQL-plus-Python pass over the pull_events union (core + gateway
arms) that grades every served abstraction by what its consumers did
next. R5 placement: grades of the AI's own writing are the AI's
self-assessment record, so abstraction_scores lives in overseer.db,
derived and regenerable from telemetry.

Signals per abstraction, rolling 7d/30d windows (plan 6.2):
  - sufficed: an org/project pull with no drill one layer below
    within the episode. Episodes are (caller_id, 15-minute
    gap-bounded window); no conversation id exists to do better.
  - drilled_past: an in-episode later pull one layer below the
    served abstraction (org -> member project; project -> member
    gist or task row; gist -> raw stays near-zero until a raw
    prefix target ships and is documented as such).
  - followups: a same-caller in-episode search AFTER the summary was
    served whose query_text shares a token with the entity key.
  - zero-recall orphan: no pulls in 30d. Ambiguous (irrelevant vs
    undiscoverable), so it is COUNTED for review, never penalized.

Weighting: signals are computed over ORGANIC pulls only
(caller_class 'organic-external', plus '' which is the core arm's
legacy empty-means-organic convention); total pulls are stored
alongside so the discount is visible, not silent.

Honest scope (plan 6.4): web/phone/desktop reads log no pulls, so
grades measure connector AIs only, which is exactly the population
the directive names.

Regeneration wiring (6.3, R6-unified): the quality trigger writes
into the SAME narrative_stale queue the fingerprint pass uses.
Fingerprint = freshness trigger; recall = quality trigger; there is
no third system. Grading also orders the refresh queue (worst-graded
with-traffic first) via overseer_state, so it changes WHICH
summaries regenerate first, not spend.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone


log = logging.getLogger("plugin.overseer.scoring")


EPISODE_GAP_MIN = 15
SCORECARD_KEY = "recall_scorecard_json"
PRIORITY_KEY = "recall_regen_priority"

ORGANIC_CLASSES = ("organic-external", "")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(s):
    try:
        dt = datetime.fromisoformat((s or "").replace(" ", "T"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def _tokens(key: str) -> set:
    return {t for t in (key or "").casefold()
            .replace("-", " ").replace("_", " ").split() if len(t) >= 3}


def run_scoring(*, db, core, cfg, summary: dict, upsert) -> None:
    """Step 9. Zero LLM; one pass; every counter lands in
    abstraction_scores plus the scorecard state key."""
    min_organic = int(cfg.get("recall_min_organic_pulls", 3))
    drill_thresh = float(cfg.get("recall_drill_ratio_threshold", 0.5))
    follow_thresh = float(cfg.get("recall_followup_ratio_threshold", 0.5))

    # ── Maps ────────────────────────────────────────────────────
    proj_org = {}
    try:
        for r in core.query("SELECT tag, org_tag FROM projects"):
            proj_org[str(r["tag"])] = r.get("org_tag") or ""
    except Exception:
        pass
    alias_map = {}
    try:
        for r in core.query(
                "SELECT alias, project_tag FROM project_aliases"):
            alias_map[r["alias"]] = r["project_tag"]
    except Exception:
        pass
    gist_proj = {}
    gist_prompt = {}
    try:
        for r in db._conn.execute(
                "SELECT id, project_tag, prompt_version_id "
                "FROM summaries_gist").fetchall():
            p = r["project_tag"] or ""
            gist_proj[int(r["id"])] = alias_map.get(p, p)
            gist_prompt[int(r["id"])] = r["prompt_version_id"] or 0
    except Exception:
        pass

    # ── Pulls (30d window, union source) ────────────────────────
    try:
        conn = db._pull_conn()
        src = db._pull_events_source()
        rows = [dict(r) for r in conn.execute(
            "SELECT {} FROM {} pe WHERE pulled_at >= "
            "datetime('now', '-30 days') ORDER BY pulled_at ASC"
            .format(db._PULL_COLS, src)).fetchall()]
    except Exception as e:
        summary["errors"].append("scoring: " + str(e)[:200])
        return

    cutoff_7d = (datetime.now(timezone.utc)
                 - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    # ── Episodes per caller ─────────────────────────────────────
    by_caller: dict = {}
    for r in rows:
        by_caller.setdefault(r.get("caller_id") or "?", []).append(r)
    episodes = []
    for caller, pulls in by_caller.items():
        cur = []
        last = None
        for p in pulls:
            ts = _parse_ts(p.get("pulled_at"))
            if (last is not None and ts is not None
                    and (ts - last).total_seconds()
                    > EPISODE_GAP_MIN * 60):
                episodes.append(cur)
                cur = []
            cur.append(p)
            last = ts or last
        if cur:
            episodes.append(cur)

    # ── Grade served abstractions ───────────────────────────────
    # scores[(level, key, window)] = [pulls, organic, sufficed,
    #                                 drilled, followups]
    scores: dict = {}

    def bump(level, key, window, organic, drilled, followed):
        s = scores.setdefault((level, key, window), [0, 0, 0, 0, 0])
        s[0] += 1
        if organic:
            s[1] += 1
            if drilled:
                s[3] += 1
            else:
                s[2] += 1
            if followed:
                s[4] += 1

    def resolve_key(pull):
        t = pull.get("artifact_table")
        k = str(pull.get("artifact_id"))
        if t == "organizations":
            return "org", k
        if t == "projects":
            return "project", alias_map.get(k, k)
        if t == "summaries_gist":
            return "gist", k
        return None, None

    gist_drilled_by_prompt: dict = {}
    for ep in episodes:
        for i, pull in enumerate(ep):
            level, key = resolve_key(pull)
            if level is None:
                continue
            organic = (pull.get("caller_class") or "") in ORGANIC_CLASSES
            drilled = False
            followed = False
            toks = _tokens(key)
            for later in ep[i + 1:]:
                lt = later.get("artifact_table")
                lk = str(later.get("artifact_id"))
                if level == "org":
                    if (lt == "projects"
                            and proj_org.get(alias_map.get(lk, lk))
                            == key):
                        drilled = True
                    if ((later.get("parent_artifact_table")
                         == "organizations")
                            and str(later.get("parent_artifact_id"))
                            == key):
                        drilled = True
                elif level == "project":
                    if (later.get("parent_artifact_table") == "projects"
                            and alias_map.get(
                                str(later.get("parent_artifact_id")),
                                str(later.get("parent_artifact_id")))
                            == key):
                        drilled = True
                    if lt == "summaries_gist":
                        try:
                            if gist_proj.get(int(lk)) == key:
                                drilled = True
                        except (TypeError, ValueError):
                            pass
                # gist -> raw: no raw prefix target yet; stays False.
                q = later.get("query_text") or ""
                if q and toks and (_tokens(q) & toks):
                    followed = True
            windows = ["30d"]
            if (pull.get("pulled_at") or "") >= cutoff_7d:
                windows.append("7d")
            for w in windows:
                bump(level, key, w, organic, drilled, followed)
            if level == "gist" and drilled:
                try:
                    pv = gist_prompt.get(int(key), 0)
                    gist_drilled_by_prompt[pv] = \
                        gist_drilled_by_prompt.get(pv, 0) + 1
                except (TypeError, ValueError):
                    pass

    # ── Persist scores (derived table: replace wholesale) ───────
    try:
        db._conn.execute("DELETE FROM abstraction_scores")
        now = _utc_iso()
        for (level, key, window), s in scores.items():
            db._conn.execute(
                "INSERT INTO abstraction_scores (level, key, window, "
                "pulls, organic_pulls, sufficed, drilled_past, "
                "followups, computed_at) VALUES (?, ?, ?, ?, ?, ?, "
                "?, ?, ?)",
                (level, key, window, s[0], s[1], s[2], s[3], s[4],
                 now))
        db._safe_commit()
    except Exception as e:
        summary["errors"].append("scoring_persist: " + str(e)[:200])
        return

    # ── gist_prompts socket (6.1/6.3): bring it live ────────────
    try:
        now = _utc_iso()
        for r in db._conn.execute(
                "SELECT id FROM gist_prompts").fetchall():
            db._conn.execute(
                "UPDATE gist_prompts SET gists_pulled_past = ?, "
                "last_signals_computed_at = ? WHERE id = ?",
                (int(gist_drilled_by_prompt.get(r["id"], 0)), now,
                 r["id"]))
        db._safe_commit()
    except Exception:
        pass

    # ── Quality trigger -> the ONE narrative_stale queue ────────
    quality_flagged_projects = 0
    quality_flagged_orgs = 0
    ranked = []
    for (level, key, window), s in scores.items():
        if window != "30d" or level == "gist":
            continue
        organic = s[1]
        if organic < min_organic:
            continue
        drill_ratio = s[3] / organic
        follow_ratio = s[4] / organic
        ranked.append((max(drill_ratio, follow_ratio), level, key))
        if drill_ratio < drill_thresh and follow_ratio < follow_thresh:
            continue
        if level == "project":
            try:
                db._conn.execute(
                    "UPDATE project_summaries SET narrative_stale = 1 "
                    "WHERE project = ? AND narrative_stale = 0",
                    (key,))
                quality_flagged_projects += 1
            except Exception:
                pass
        elif level == "org":
            out = upsert("org_summaries",
                         {"org_tag": key, "narrative_stale": 1})
            if out.get("ok"):
                quality_flagged_orgs += 1
    try:
        db._safe_commit()
    except Exception:
        pass

    # Regen priority: worst-graded-with-traffic first. The narrative
    # refresh step consults this ordering.
    ranked.sort(reverse=True)
    try:
        db.set_overseer_state(PRIORITY_KEY, json.dumps(
            [key for _, level, key in ranked if level == "project"]))
    except Exception:
        pass

    # ── Scorecard (WM block; honest caveats stated) ─────────────
    proj_30 = [(key, s) for (level, key, w), s in scores.items()
               if level == "project" and w == "30d"
               and s[1] >= min_organic]

    def ratio(s):
        return s[2] / s[1] if s[1] else 0.0

    proj_30.sort(key=lambda kv: ratio(kv[1]))
    fmt = [{"key": k, "organic_pulls": s[1],
            "sufficed_ratio": round(ratio(s), 2),
            "drilled_past": s[3], "followups": s[4]}
           for k, s in proj_30]
    pulled_keys = {key for (level, key, w) in scores
                   if level == "project"}
    orphans = sorted(t for t in proj_org
                     if alias_map.get(t, t) not in pulled_keys)
    scorecard = {
        "computed_at": _utc_iso(),
        "graded_projects": len(proj_30),
        "worst_5": fmt[:5],
        "best_5": fmt[-5:][::-1],
        "zero_recall_orphans_30d": len(orphans),
        "orphan_sample": orphans[:10],
        "quality_flagged": {"projects": quality_flagged_projects,
                            "orgs": quality_flagged_orgs},
        "caveats": ("grades cover connector AIs only (web/phone/"
                    "desktop reads log no pulls); gist-to-raw drills "
                    "unmeasurable until a raw prefix target ships"),
    }
    try:
        db.set_overseer_state(SCORECARD_KEY, json.dumps(scorecard))
    except Exception:
        pass

    summary["recall_scores_written"] = len(scores)
    summary["recall_quality_flagged"] = (quality_flagged_projects
                                         + quality_flagged_orgs)
    summary["recall_orphans_30d"] = len(orphans)
