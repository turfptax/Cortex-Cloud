"""Exfiltration monitor over the corpus read log (`pull_events`).

Every corpus read records a row via corpus_service._record_pull. This module
reads that log and flags patterns that look like a leaked connector key being
used to exfiltrate the corpus, rather than a connector serving normal queries.

Threat model (single-user system, a handful of legitimate connector keys):
the dangerous signal is BREADTH and VOLUME from one key in a short window. A
real query returns a few artifacts; a scraper walks the whole corpus. Plus
two cheap identity signals: a brand-new caller, and a long-dormant key waking
up. Off-hours alone is a weak signal for one user across timezones, so it is
reported as context, never as a standalone alert.

Pure read-only analysis; no side effects. Shared by scripts/monitor_pulls.py
(headless/cron) and the /admin/monitor endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from . import db

# Defaults tuned for a personal corpus with a few connectors. A normal MCP
# search returns <= ~55 artifacts (11 kinds x 5); sustained pulls far above
# that from one key in an hour is scraping, not querying.
SCRAPE_ARTIFACTS_PER_HOUR = 300   # distinct artifacts one caller pulls / hour
VOLUME_PULLS_PER_HOUR = 800       # raw pull rows one caller emits / hour
DORMANT_DAYS = 14                 # a key silent this long then active = notable
BASELINE_DAYS = 7                 # trailing window for "normal" per caller


@dataclass
class Alert:
    severity: str           # "high" | "medium" | "info"
    kind: str               # scrape | volume | dormant | new_caller | offhours
    caller: str
    detail: str
    evidence: dict = field(default_factory=dict)


def _rows(sql: str, **params):
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(sa.text(sql), params).mappings()]


def analyze(*, window_hours: int = 1, now: datetime | None = None) -> dict:
    """Scan pull_events for the trailing `window_hours` and return alerts plus
    a per-caller activity summary. Returns {} gracefully if logging is off."""
    if not db.has_table("pull_events"):
        return {"ok": False, "error": "pull_events table missing (logging off)",
                "alerts": [], "summary": []}

    now = now or datetime.now(timezone.utc)

    # Dialect split. The Azure SQL migration named the timestamp column
    # `ts`; the SQLite shapes (gateway.db in ATTACH mode, single-file
    # dev) use the core-schema name `pulled_at` - and SQLite has neither
    # DATEPART nor guaranteed CONCAT. Same analysis, two SQL spellings.
    sqlite = db.is_sqlite()
    cols = db.columns("pull_events")
    ts = "pulled_at" if "pulled_at" in cols else "ts"
    has_ip = "source_ip" in cols
    ips_expr = "COUNT(DISTINCT source_ip)" if has_ip else "0"
    if sqlite:
        concat = "artifact_table || ':' || artifact_id"
        hour_bucket = f"strftime('%Y-%j %H', {ts})"
        # Stored timestamps are naive UTC ('YYYY-MM-DD HH:MM:SS'); bind
        # naive so text comparison stays lexicographic-correct.
        now = now.replace(tzinfo=None)
    else:
        concat = "CONCAT(artifact_table, ':', artifact_id)"
        hour_bucket = (f"DATEPART(year,{ts}), DATEPART(dayofyear,{ts}), "
                       f"DATEPART(hour,{ts})")
    win_start = now - timedelta(hours=window_hours)
    base_start = now - timedelta(days=BASELINE_DAYS)
    alerts: list[Alert] = []

    # ── per-caller activity in the alert window ──────────────────────
    window = _rows(
        f"""SELECT caller_id,
                  COUNT(*) AS pulls,
                  COUNT(DISTINCT {concat}) AS artifacts,
                  {ips_expr} AS ips,
                  MIN({ts}) AS first_ts, MAX({ts}) AS last_ts
           FROM pull_events
           WHERE {ts} >= :w
           GROUP BY caller_id""",
        w=win_start)

    # ── trailing baseline: each caller's busiest historical hour ─────
    # If this hour dwarfs the caller's own busy-hour history, that's a spike
    # even when it sits under the absolute thresholds.
    if sqlite:
        base_sql = f"""SELECT caller_id, MAX(cnt) AS peak_hour,
                              AVG(cnt*1.0) AS avg_hour
           FROM (
             SELECT caller_id, {hour_bucket} AS bucket, COUNT(*) cnt
             FROM pull_events
             WHERE {ts} >= :b AND {ts} < :w
             GROUP BY caller_id, {hour_bucket}
           ) g GROUP BY caller_id"""
    else:
        base_sql = f"""SELECT caller_id, MAX(cnt) AS peak_hour,
                              AVG(cnt*1.0) AS avg_hour
           FROM (
             SELECT caller_id, DATEPART(year,{ts}) y,
                    DATEPART(dayofyear,{ts}) d,
                    DATEPART(hour,{ts}) h, COUNT(*) cnt
             FROM pull_events
             WHERE {ts} >= :b AND {ts} < :w
             GROUP BY caller_id, DATEPART(year,{ts}),
                      DATEPART(dayofyear,{ts}), DATEPART(hour,{ts})
           ) g GROUP BY caller_id"""
    base = {r["caller_id"]: r for r in _rows(
        base_sql, b=base_start, w=win_start)}

    # ── callers ever seen before this window ─────────────────────────
    known = {r["caller_id"] for r in _rows(
        f"SELECT DISTINCT caller_id FROM pull_events WHERE {ts} < :w",
        w=win_start)}

    for r in window:
        caller = r["caller_id"] or "(anonymous)"
        pulls, arts = r["pulls"], r["artifacts"]
        per_hour = pulls / window_hours
        arts_per_hour = arts / window_hours

        if arts_per_hour >= SCRAPE_ARTIFACTS_PER_HOUR:
            alerts.append(Alert(
                "high", "scrape", caller,
                f"pulled {arts} distinct artifacts in {window_hours}h "
                f"({arts_per_hour:.0f}/h, threshold {SCRAPE_ARTIFACTS_PER_HOUR})",
                {"artifacts": arts, "pulls": pulls}))
        elif per_hour >= VOLUME_PULLS_PER_HOUR:
            alerts.append(Alert(
                "high", "volume", caller,
                f"{pulls} reads in {window_hours}h "
                f"({per_hour:.0f}/h, threshold {VOLUME_PULLS_PER_HOUR})",
                {"pulls": pulls, "artifacts": arts}))
        else:
            b = base.get(r["caller_id"])
            if b and b["peak_hour"] and pulls > 4 * b["peak_hour"] and pulls >= 50:
                alerts.append(Alert(
                    "medium", "volume", caller,
                    f"{pulls} reads this window vs prior busiest hour "
                    f"{int(b['peak_hour'])} (4x spike)",
                    {"pulls": pulls, "prior_peak_hour": int(b["peak_hour"])}))

        if r["caller_id"] and r["caller_id"] not in known and pulls >= 10:
            alerts.append(Alert(
                "medium", "new_caller", caller,
                f"caller never seen before this window, {pulls} reads",
                {"pulls": pulls}))

        if r["ips"] and r["ips"] > 1:
            alerts.append(Alert(
                "medium", "multi_ip", caller,
                f"key used from {r['ips']} distinct source IPs in the window",
                {"ips": r["ips"]}))

    # ── dormant key reactivation (uses gateway_tokens.last_used_at) ──
    if db.has_table("gateway_tokens"):
        active_callers = {r["caller_id"] for r in window if r["caller_id"]}
        for tok in _rows(
            """SELECT name, key_prefix, last_used_at, revoked_at
               FROM gateway_tokens WHERE revoked_at IS NULL"""):
            cid = f"token:"  # caller_id is token:<id>:<name>; match by name tail
            name = tok["name"]
            hit = next((c for c in active_callers if c.endswith(f":{name}")), None)
            if not hit:
                continue
            lu = tok["last_used_at"]
            if isinstance(lu, str):
                try:
                    lu = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                except ValueError:
                    lu = None
            if lu and lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            # last_used_at updates on every call, so "dormant" means: active now
            # but no pull_events in the trailing baseline window.
            prior = _rows(
                f"SELECT COUNT(*) AS n FROM pull_events "
                f"WHERE caller_id = :c AND {ts} < :w AND {ts} >= :b",
                c=hit, w=win_start, b=base_start)
            if prior and prior[0]["n"] == 0:
                alerts.append(Alert(
                    "medium", "dormant", hit,
                    f"key '{name}' active now but silent the prior {BASELINE_DAYS}d",
                    {"key_prefix": tok["key_prefix"]}))

    alerts.sort(key=lambda a: {"high": 0, "medium": 1, "info": 2}[a.severity])
    return {
        "ok": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "window_hours": window_hours,
        "alert_count": len(alerts),
        "highest": alerts[0].severity if alerts else "none",
        "alerts": [a.__dict__ for a in alerts],
        "summary": sorted(window, key=lambda r: -r["pulls"]),
    }
