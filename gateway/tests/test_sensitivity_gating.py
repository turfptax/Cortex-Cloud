"""Sensitivity gating wired into the read path (corpus_service).

Verifies decide()/apply() now actually gate reads: a connector token's
max_tier ceiling is enforced, raw is withheld from connectors, and the
category allow-list bites. Untagged interpretive rows still pass through
as `full` (no regression on today's corpus).
"""
import pytest

from cortex_gateway import corpus_service as cs
from cortex_gateway.auth import Principal


@pytest.fixture(autouse=True)
def _granted_connector(monkeypatch):
    # These tests exercise the sensitivity TIER + category logic. Assume the
    # connector's connection is granted full access; the grant gate (default-deny
    # for ungranted connectors) is covered in test_connector_grants.
    monkeypatch.setattr(cs.grants, "has_full_access", lambda p: True)


def _connector(max_tier="internal", cats=None):
    return Principal(id=1, name="openclaw", kind="connector",
                     scopes={"connector:read"}, max_tier=max_tier,
                     category_filter=cats or [])


def _app(max_tier="restricted"):
    return Principal(id=2, name="hub", kind="app", scopes={"app"},
                     max_tier=max_tier, category_filter=[])


# ── _gate_decision matrix ─────────────────────────────────────────────

def test_untagged_row_is_full():
    # Today's interpretive rows carry no tier -> default internal -> full.
    assert cs._gate_decision(_connector(), "summaries_gist", {"body": "x"}) == "full"


def test_confidential_withheld_from_internal_connector():
    row = {"body": "a person grief processing", "sensitivity_tier": "confidential"}
    assert cs._gate_decision(_connector("internal"), "summaries_gist", row) == "withheld"


def test_confidential_sanitized_for_app_with_high_ceiling():
    row = {"body": "x", "sensitivity_tier": "confidential"}
    # is_connector False (app) + ceiling restricted -> global policy: sanitized.
    assert cs._gate_decision(_app("restricted"), "summaries_gist", row) == "sanitized"


def test_raw_withheld_from_connector():
    row = {"body": "raw transcript", "sensitivity_tier": "internal"}
    assert cs._gate_decision(_connector("restricted"), "imported_sessions", row) == "withheld"


def test_category_filter_withholds_out_of_scope():
    row = {"body": "x", "category": "personal"}
    p = _connector("restricted", cats=["work", "research"])
    assert cs._gate_decision(p, "summaries_gist", row) == "withheld"


def test_category_filter_allows_in_scope():
    row = {"body": "x", "category": "work"}
    p = _connector("restricted", cats=["work", "research"])
    assert cs._gate_decision(p, "summaries_gist", row) == "full"


# ── fetch() end-to-end redaction ──────────────────────────────────────

def test_fetch_redacts_confidential_body(monkeypatch):
    monkeypatch.setattr(cs.db, "has_table", lambda t: t != "pull_events")
    monkeypatch.setattr(cs.db, "fetchone", lambda sql, params: {
        "id": 7, "body": "SECRET confidential body",
        "sensitivity_tier": "confidential"})
    out = cs.fetch(_connector("internal"), "g:7")
    assert out["ok"] and out["gated"] and out["gate"] == "withheld"
    # The confidential body must NOT be present anywhere in the payload.
    assert "SECRET" not in str(out)


def test_fetch_full_for_untagged(monkeypatch):
    monkeypatch.setattr(cs.db, "has_table", lambda t: t != "pull_events")
    monkeypatch.setattr(cs.db, "fetchone", lambda sql, params: {
        "id": 8, "body": "ordinary gist body"})
    out = cs.fetch(_connector("internal"), "g:8")
    assert out["ok"] and not out.get("gated")
    assert out["primary"]["body"] == "ordinary gist body"
