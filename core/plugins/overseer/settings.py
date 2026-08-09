"""Runtime overseer settings - a per-instance overlay on plugin.toml.

plugin.toml ships with the CODE: same file, same defaults, for every
install of the public repo. The dials an owner actually wants to turn
per-instance (which model does the thinking, how much the loop may
spend, what it ingests) are INSTANCE state, so they live in the
overseer_state KV table in the corpus database and override the
manifest at read time. Changing them from the web Hub takes effect on
the next LLM call or tick; no rebuild, no restart, and nothing
instance-specific ever lands in the repo.

Design:
  - Allowlist, not free-form. Only keys in SETTINGS_SCHEMA can be set.
    The HTTP surface is owner-authed, but a tight allowlist means even
    a hijacked session cannot rewire backend URLs or secrets paths.
  - Overrides live in ONE overseer_state row as a JSON object. A key
    that is absent means "use the manifest value"; reset deletes the
    key. plugin.toml stays the complete, documented default set.
  - Reads are cached in memory and invalidated on write, so the router
    and the loop can consult settings on every call/tick for free.
  - Validation is all-or-nothing: one bad key rejects the whole batch,
    so a typo can't half-apply a settings change.

Wiring (see __init__.on_load):
  - LLMRouter takes settings= and consults it for the default model and
    the per-purpose model_overrides at call time.
  - self.api.config is replaced with LayeredConfig(settings, config) so
    every existing config consumer (loop budgets, journal gate, ingest
    lists) honors overrides without knowing they exist.
"""

from __future__ import annotations

import json
import logging
import threading


log = logging.getLogger("plugin.overseer.settings")


# The single overseer_state row holding all overrides, as JSON.
STATE_KEY = "runtime_settings_overrides"


# Allowlisted keys. "llm.*" keys overlay the [llm] manifest table and
# are consulted by LLMRouter; everything else overlays [config] and
# flows through LayeredConfig.get. Defaults are NOT duplicated here -
# they come from the manifest at read time, so plugin.toml remains the
# single documented source of defaults.
#
# type vocabulary:
#   model       - one OpenRouter model slug (free string, validated light)
#   model_map   - partial {purpose: model} dict over the manifest's
#                 [llm.model_overrides] purposes
#   int/number  - numeric with min/max bounds
#   bool        - strict true/false
#   string_list - list of non-empty strings (repos, channels)
SETTINGS_SCHEMA = {
    "llm.model": {
        "section": "brain",
        "type": "model",
        "label": "Main model",
        "help": "Default model for every overseer task that has no "
                "per-task override below. This is the brain.",
    },
    "llm.model_overrides": {
        "section": "brain",
        "type": "model_map",
        "label": "Per-task models",
        "help": "Route individual tasks to their own model. Unset tasks "
                "fall back to the main model.",
    },
    "tick_interval_s": {
        "section": "loop",
        "type": "int",
        "label": "Tick interval (seconds)",
        "help": "How often the background loop wakes up.",
        "min": 60,
        "max": 86400,
    },
    "loop_max_llm_calls_per_tick": {
        "section": "loop",
        "type": "int",
        "label": "Max LLM calls per tick",
        "min": 1,
        "max": 1000,
    },
    "loop_max_cost_usd_per_tick": {
        "section": "loop",
        "type": "number",
        "label": "Max cost per tick (USD)",
        "min": 0.01,
        "max": 25.0,
    },
    "loop_daily_budget_usd": {
        "section": "loop",
        "type": "number",
        "label": "Daily budget (USD)",
        "help": "Hard daily ceiling across all loop work. The manual "
                "budget override on the Corpus page can raise it for "
                "one day; this dial moves the everyday ceiling.",
        "min": 0.01,
        "max": 100.0,
    },
    "loop_daily_budget_calls": {
        "section": "loop",
        "type": "int",
        "label": "Daily budget (calls)",
        "min": 1,
        "max": 100000,
    },
    "loop_journal_enabled": {
        "section": "loop",
        "type": "bool",
        "label": "Overseer journal",
        "help": "First-person reflection after notable ticks.",
    },
    "loop_checkin_enabled": {
        "section": "loop",
        "type": "bool",
        "label": "Data-triggered check-in",
        "help": "Status note written only when new owner data (notes, "
                "journal, phone sync) arrived since the last one.",
    },
    "loop_checkin_min_hours": {
        "section": "loop",
        "type": "number",
        "label": "Check-in spacing (hours)",
        "help": "Minimum hours between check-ins even while data keeps "
                "arriving.",
        "min": 0.5,
        "max": 48.0,
    },
    "working_memory_reminder_max_age_days": {
        "section": "loop",
        "type": "int",
        "label": "Reminder max age (days)",
        "help": "Reminder notes older than this stop surfacing as open "
                "in working memory and chat context.",
        "min": 1,
        "max": 365,
    },
    "loop_git_ingest_repos": {
        "section": "ingest",
        "type": "string_list",
        "label": "Git repos",
        "help": "owner/name per line. Commits become part of the corpus "
                "(what you ship, not just what you say).",
    },
    "loop_youtube_channels": {
        "section": "ingest",
        "type": "string_list",
        "label": "YouTube channels",
        "help": "persona:channel_id[:project_tag] per line. Public RSS "
                "poll, no API key.",
    },
}

_MAX_MODEL_LEN = 200
_MAX_LIST_LEN = 200
_MAX_LIST_ITEM_LEN = 300


def _validate_model(key, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{}: model must be a non-empty string".format(key))
    value = value.strip()
    if len(value) > _MAX_MODEL_LEN:
        raise ValueError("{}: model id too long".format(key))
    if any(c.isspace() for c in value):
        raise ValueError("{}: model id must not contain spaces".format(key))
    return value


def _validate_number(key, value, spec, *, integer):
    ok_type = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not ok_type:
        raise ValueError("{}: must be a number".format(key))
    if integer:
        if float(value) != int(value):
            raise ValueError("{}: must be a whole number".format(key))
        value = int(value)
    else:
        value = float(value)
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and value < lo:
        raise ValueError("{}: must be >= {}".format(key, lo))
    if hi is not None and value > hi:
        raise ValueError("{}: must be <= {}".format(key, hi))
    return value


def _validate_string_list(key, value):
    if not isinstance(value, list):
        raise ValueError("{}: must be a list of strings".format(key))
    if len(value) > _MAX_LIST_LEN:
        raise ValueError("{}: too many entries (max {})".format(
            key, _MAX_LIST_LEN))
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("{}: entries must be strings".format(key))
        item = item.strip()
        if not item:
            continue
        if len(item) > _MAX_LIST_ITEM_LEN:
            raise ValueError("{}: entry too long".format(key))
        out.append(item)
    return out


class OverseerSettings:
    """Validated, persisted overrides over the plugin.toml manifest."""

    def __init__(self, *, db, manifest_llm=None, manifest_config=None):
        """
        db: OverseerDB (get/set/delete_overseer_state). May be None in
            tests that only exercise validation; persistence then no-ops.
        manifest_llm: parsed [llm] table from plugin.toml.
        manifest_config: parsed [config] table from plugin.toml.
        """
        self._db = db
        self._manifest_llm = dict(manifest_llm or {})
        self._manifest_config = dict(manifest_config or {})
        self._lock = threading.Lock()
        self._cache = None

    # ── Storage ─────────────────────────────────────────────────

    def overrides(self):
        """The stored override dict (cached; never None)."""
        with self._lock:
            if self._cache is not None:
                return self._cache
            data = {}
            if self._db is not None:
                try:
                    raw = self._db.get_overseer_state(STATE_KEY)
                    if raw:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            # Drop keys that left the allowlist since they
                            # were written - schema is the contract.
                            data = {k: v for k, v in parsed.items()
                                    if k in SETTINGS_SCHEMA}
                except Exception as e:
                    log.warning("could not load %s: %s", STATE_KEY, e)
            self._cache = data
            return data

    def _save(self, data):
        with self._lock:
            self._cache = dict(data)
            if self._db is None:
                return
            if data:
                self._db.set_overseer_state(STATE_KEY, json.dumps(data))
            else:
                try:
                    self._db.delete_overseer_state(STATE_KEY)
                except Exception:
                    self._db.set_overseer_state(STATE_KEY, "{}")

    # ── Typed reads ─────────────────────────────────────────────

    def llm_override(self, key):
        """Runtime override for an [llm] manifest key, or None."""
        return self.overrides().get("llm." + key)

    def effective_model_overrides(self):
        """Manifest [llm.model_overrides] with runtime picks layered on."""
        base = dict(self._manifest_llm.get("model_overrides") or {})
        stored = self.overrides().get("llm.model_overrides") or {}
        for purpose, model in stored.items():
            if model:
                base[purpose] = model
        return base

    def config_value(self, key, default=None):
        """Override else manifest [config] else default."""
        ov = self.overrides()
        if key in ov:
            return ov[key]
        return self._manifest_config.get(key, default)

    # ── Writes ──────────────────────────────────────────────────

    def apply(self, *, sets=None, resets=None):
        """Validate and persist a batch. All-or-nothing: any invalid
        key/value raises ValueError and nothing is written."""
        sets = dict(sets or {})
        resets = list(resets or [])

        validated = {}
        for key, value in sets.items():
            if key not in SETTINGS_SCHEMA:
                raise ValueError("unknown setting: {}".format(key))
            validated[key] = self._validate(key, value)
        for key in resets:
            if key not in SETTINGS_SCHEMA:
                raise ValueError("unknown setting: {}".format(key))

        data = dict(self.overrides())
        for key in resets:
            data.pop(key, None)
        for key, value in validated.items():
            data[key] = value
        self._save(data)
        log.info("settings applied: set=%s reset=%s",
                 sorted(validated.keys()), sorted(resets))
        return data

    def _validate(self, key, value):
        spec = SETTINGS_SCHEMA[key]
        t = spec["type"]
        if t == "model":
            return _validate_model(key, value)
        if t == "model_map":
            if not isinstance(value, dict):
                raise ValueError(
                    "{}: must be an object of purpose->model".format(key))
            known = set(
                (self._manifest_llm.get("model_overrides") or {}).keys())
            out = {}
            for purpose, model in value.items():
                if purpose not in known:
                    raise ValueError("unknown task purpose: {}".format(purpose))
                if model in (None, ""):
                    continue
                out[purpose] = _validate_model(
                    "{}[{}]".format(key, purpose), model)
            if not out:
                raise ValueError(
                    "{}: empty - use reset to clear".format(key))
            return out
        if t == "int":
            return _validate_number(key, value, spec, integer=True)
        if t == "number":
            return _validate_number(key, value, spec, integer=False)
        if t == "bool":
            if not isinstance(value, bool):
                raise ValueError("{}: must be true or false".format(key))
            return value
        if t == "string_list":
            return _validate_string_list(key, value)
        raise ValueError("{}: unhandled type {}".format(key, t))

    # ── API snapshot ────────────────────────────────────────────

    def describe(self):
        """Everything the settings UI needs: schema + defaults +
        overrides + effective values, in schema order."""
        ov = self.overrides()
        keys = []
        for key, spec in SETTINGS_SCHEMA.items():
            if key == "llm.model":
                default = self._manifest_llm.get("model")
                effective = ov.get(key, default)
            elif key == "llm.model_overrides":
                default = dict(self._manifest_llm.get("model_overrides") or {})
                effective = self.effective_model_overrides()
            else:
                default = self._manifest_config.get(key)
                effective = ov.get(key, default)
            entry = {
                "key": key,
                "section": spec["section"],
                "type": spec["type"],
                "label": spec["label"],
                "help": spec.get("help", ""),
                "default": default,
                "override": ov.get(key),
                "effective": effective,
            }
            for bound in ("min", "max"):
                if bound in spec:
                    entry[bound] = spec[bound]
            keys.append(entry)
        return {"keys": keys}


class LayeredConfig:
    """PluginConfig-compatible view: runtime overrides first, then the
    manifest config the runtime handed the plugin.

    Handed to every existing config consumer in place of api.config, so
    the loop's budgets, the journal gate and the ingest lists pick up
    override changes on their next read without code changes. The
    "llm.*" override keys are namespaced, so they can never shadow a
    [config] key.
    """

    def __init__(self, settings, base):
        self._settings = settings
        self._base = base

    def get(self, key, default=None):
        ov = self._settings.overrides()
        if key in ov:
            return ov[key]
        if self._base is None:
            return default
        try:
            return self._base.get(key, default)
        except Exception:
            return default

    def __contains__(self, key):
        if key in self._settings.overrides():
            return True
        try:
            return self._base is not None and key in self._base
        except Exception:
            return False

    def __repr__(self):
        return "LayeredConfig({} overrides over {!r})".format(
            len(self._settings.overrides()), self._base)
