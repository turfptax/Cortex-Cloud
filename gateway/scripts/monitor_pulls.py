#!/usr/bin/env python3
"""Headless exfiltration check over the Gateway's pull_events log.

Runs the monitor analyzer and prints a human report. Exit code 2 if any
high-severity alert fires, 1 for medium, 0 for clean - so it can gate a cron
job or CI step and page only when something real shows up.

Run (needs DB_URL pointing at the canonical store):
    DB_URL=... python scripts/monitor_pulls.py [--hours 1] [--json]

Cron example (hourly, alert to stderr -> mail):
    0 * * * * cd /app && DB_URL=... python scripts/monitor_pulls.py || \
        echo "Cortex Gateway monitor flagged something" | mail -s alert you@x
"""
from __future__ import annotations

import argparse
import json
import sys

from cortex_gateway import monitor

SEV_EXIT = {"high": 2, "medium": 1, "info": 0, "none": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = monitor.analyze(window_hours=args.hours)
    if args.json:
        print(json.dumps(rep, indent=1, default=str))
        return SEV_EXIT.get(rep.get("highest", "none"), 0)

    if not rep.get("ok"):
        print(f"monitor unavailable: {rep.get('error')}")
        return 0

    print(f"Cortex Gateway read monitor - {rep['generated_at']} "
          f"(last {rep['window_hours']}h)")
    summary = rep["summary"]
    if not summary:
        print("  no corpus reads in the window.")
    else:
        print(f"  {len(summary)} active caller(s):")
        for r in summary:
            print(f"    {r['caller_id'] or '(anon)':45s} "
                  f"{r['pulls']:>5} reads  {r['artifacts']:>5} artifacts  "
                  f"{r['ips'] or 0} ip(s)")

    alerts = rep["alerts"]
    if not alerts:
        print("  OK - no anomalies.")
    else:
        print(f"\n  {len(alerts)} ALERT(S):")
        for a in alerts:
            mark = {"high": "!!", "medium": " >", "info": "  "}.get(a["severity"], "  ")
            print(f"  {mark} [{a['severity']}] {a['kind']} - {a['caller']}")
            print(f"      {a['detail']}")
    return SEV_EXIT.get(rep.get("highest", "none"), 0)


if __name__ == "__main__":
    sys.exit(main())
