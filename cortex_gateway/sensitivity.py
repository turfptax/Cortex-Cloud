"""Sensitivity gating (Slice 13), enforced at the Gateway boundary on every
read path (REST + MCP).

Tiers, ordered low→high exposure-risk:
    public  <  internal  <  confidential  <  restricted

Global read policy:
  - public / internal   → full body
  - confidential         → sanitized body, title preserved
  - restricted           → title-only stub
  - raw layer            → never leaves via connectors by default

A connector/app token additionally carries a `max_tier` ceiling. The
effective decision is the MORE restrictive of (global policy, token ceiling):
a token with ceiling=internal sees confidential/restricted as withheld.

Most interpretive artifacts (gists/themes/journal) carry no per-row tier - they are already-processed summaries and pass through as `full`. Gating bites
hardest on raw fetches (imported_sessions), which carry an explicit tier.
"""
from __future__ import annotations

_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

# Default decision per tier when the token ceiling does not further restrict.
_POLICY = {
    "public": "full",
    "internal": "full",
    "confidential": "sanitized",
    "restricted": "title_only",
}


def normalize_tier(tier: str | None) -> str:
    if not tier:
        return "internal"  # untagged content defaults to internal, not public
    t = str(tier).strip().lower()
    return t if t in _ORDER else "internal"


def decide(tier: str | None, token_ceiling: str | None, *, is_raw: bool = False,
           is_connector: bool = False) -> str:
    """Return one of: full | sanitized | title_only | withheld.

    Args:
      tier: the artifact's sensitivity tier.
      token_ceiling: the calling token's max_tier (None = no ceiling).
      is_raw: True for layer-3 raw content (imported_sessions / files).
      is_connector: True when the caller is an external AI connector
        (raw is withheld from connectors by default).
    """
    t = normalize_tier(tier)

    if is_raw and is_connector:
        # Raw never leaves via connectors by default; allow only public raw.
        if t != "public":
            return "withheld"

    decision = _POLICY[t]

    if token_ceiling:
        ceiling = normalize_tier(token_ceiling)
        if _ORDER[t] > _ORDER[ceiling]:
            # Above the token's ceiling - withhold the body entirely.
            return "withheld" if decision != "title_only" else "title_only"

    return decision


def apply(decision: str, *, title: str, body: str) -> dict:
    """Shape a gated payload for return to the caller."""
    if decision == "full":
        return {"title": title, "body": body, "gated": False}
    if decision == "sanitized":
        return {"title": title, "body": "[confidential - sanitized at gateway]",
                "gated": True, "gate": "sanitized"}
    if decision == "title_only":
        return {"title": title, "body": None, "gated": True, "gate": "title_only"}
    return {"title": None, "body": None, "gated": True, "gate": "withheld"}
