"""Runtime settings: the per-instance overlay actually overlays.

Added 2026-08-08. plugin.toml ships identical defaults to every install;
the owner's model picks and loop budgets live in overseer_state and win
at read time. What has to hold:

  - no overrides: every read falls through to the manifest untouched
  - a set model wins in the router's resolution, per-task map merges
    partially, and both survive a process restart (fresh instance)
  - reset returns a key to the manifest value and deletes the row when
    the last override goes
  - validation is all-or-nothing: one bad key in a batch leaves the
    stored state exactly as it was
  - LayeredConfig serves overrides first, then the wrapped base config
  - a router built without settings keeps pure-manifest behavior

Run: python scripts/test_overseer_settings.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "overseer"))
sys.path.insert(0, str(ROOT / "src"))

from overseer_db import OverseerDB          # noqa: E402
from plugin_api import PluginConfig         # noqa: E402
from settings import (                      # noqa: E402
    LayeredConfig, OverseerSettings, STATE_KEY,
)
from llm_router import LLMRouter            # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


MANIFEST_LLM = {
    "backend": "openrouter",
    "model": "anthropic/claude-opus-4.7",
    "model_overrides": {
        "overseer-chat": "anthropic/claude-opus-4.7",
        "auto-tag-notes": "google/gemini-2.5-flash",
    },
}
MANIFEST_CONFIG = {
    "tick_interval_s": 900,
    "loop_daily_budget_usd": 3.00,
    "loop_journal_enabled": True,
    "loop_git_ingest_repos": ["someone/some-repo"],
}


def fresh(db):
    return OverseerSettings(
        db=db, manifest_llm=MANIFEST_LLM, manifest_config=MANIFEST_CONFIG)


def main():
    tmp = tempfile.mkdtemp(prefix="overseer-settings-test-")
    db = OverseerDB(str(Path(tmp) / "test.db"))
    try:
        run_checks(db)
    finally:
        db.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("all passed")


def run_checks(db):
    s = fresh(db)

    print("defaults fall through untouched:")
    check("no model override", s.llm_override("model") is None)
    check("manifest overrides map",
          s.effective_model_overrides() == MANIFEST_LLM["model_overrides"])
    check("config passthrough", s.config_value("tick_interval_s") == 900)
    snap = {e["key"]: e for e in s.describe()["keys"]}
    check("describe default model",
          snap["llm.model"]["default"] == "anthropic/claude-opus-4.7")
    check("describe no override", snap["llm.model"]["override"] is None)

    print("set + merge + persistence:")
    s.apply(sets={
        "llm.model": "moonshotai/kimi-k3",
        "llm.model_overrides": {"auto-tag-notes": "z-ai/glm-5.2"},
        "loop_daily_budget_usd": 5.0,
        "loop_git_ingest_repos": ["  a/b  ", "", "c/d"],
    })
    check("model override read", s.llm_override("model") == "moonshotai/kimi-k3")
    eff = s.effective_model_overrides()
    check("map merges partially",
          eff["auto-tag-notes"] == "z-ai/glm-5.2"
          and eff["overseer-chat"] == "anthropic/claude-opus-4.7")
    check("list entries stripped",
          s.config_value("loop_git_ingest_repos") == ["a/b", "c/d"])

    s2 = fresh(db)
    check("fresh instance re-reads",
          s2.llm_override("model") == "moonshotai/kimi-k3")

    print("router resolution:")
    router = LLMRouter(manifest_llm=MANIFEST_LLM, db=None, settings=s)
    check("router default model",
          router._default_model("x") == "moonshotai/kimi-k3")
    check("router purpose map",
          router._model_overrides()["auto-tag-notes"] == "z-ai/glm-5.2")
    bare = LLMRouter(manifest_llm=MANIFEST_LLM, db=None)
    check("router without settings",
          bare._default_model("x") == "anthropic/claude-opus-4.7"
          and bare._model_overrides() == MANIFEST_LLM["model_overrides"])

    print("layered config:")
    layered = LayeredConfig(s, PluginConfig(MANIFEST_CONFIG))
    check("override wins", layered.get("loop_daily_budget_usd") == 5.0)
    check("base fallback", layered.get("loop_journal_enabled") is True)
    check("default fallback", layered.get("no_such_key", "d") == "d")
    check("contains override", "loop_daily_budget_usd" in layered)
    check("contains base", "tick_interval_s" in layered)
    # "llm.*" overrides are namespaced: a config consumer asking for
    # "model" must never see the brain's model pick.
    check("llm keys never shadow config", layered.get("model") is None)

    print("reset:")
    s.apply(resets=["llm.model", "llm.model_overrides",
                    "loop_git_ingest_repos"])
    check("model back to manifest", s.llm_override("model") is None)
    check("map back to manifest",
          s.effective_model_overrides() == MANIFEST_LLM["model_overrides"])
    s.apply(resets=["loop_daily_budget_usd"])
    check("row deleted when last override goes",
          db.get_overseer_state(STATE_KEY) in (None, "{}"))

    print("validation is all-or-nothing:")
    s.apply(sets={"loop_daily_budget_usd": 2.0})
    for label, sets in [
        ("unknown key", {"nope": 1}),
        ("space in model", {"llm.model": "not a model"}),
        ("non-numeric", {"loop_daily_budget_usd": "three"}),
        ("out of bounds", {"tick_interval_s": 5}),
        ("float where int", {"loop_daily_budget_calls": 1.5}),
        ("unknown purpose", {"llm.model_overrides": {"bogus": "m/x"}}),
        ("non-bool", {"loop_journal_enabled": "yes"}),
        ("bad batch reverts whole", {"loop_daily_budget_usd": 9.0, "x": 1}),
    ]:
        try:
            s.apply(sets=sets)
            check(label, False, "no ValueError raised")
        except ValueError:
            check(label, True)
    check("stored state untouched by bad batches",
          s.config_value("loop_daily_budget_usd") == 2.0
          and s.overrides().get("loop_daily_budget_usd") == 2.0)


if __name__ == "__main__":
    main()
