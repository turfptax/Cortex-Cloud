"""Search + token maps, ported from cortex-core (corpus.py / detail.py) so the
Gateway is self-contained and dialect-portable - no SQLite-only dependency on
the overseer plugin at runtime.

SEARCH_TARGETS: kind_key -> (table, body_columns, token_prefix, kind_label)
"""
from __future__ import annotations

SEARCH_TARGETS: dict[str, tuple[str, list[str], str, str]] = {
    "gist":      ("summaries_gist",        ["body"],               "g",   "gist"),
    "theme":     ("summaries_theme",       ["title", "body"],      "t",   "theme"),
    "episode":   ("summaries_episode",     ["title", "body"],      "e",   "episode"),
    "pattern":   ("patterns",              ["name", "body"],       "p",   "pattern"),
    "drift":     ("drift_observations",    ["body", "direction"],  "d",   "drift"),
    "note":      ("future_overseer_notes", ["body"],               "n",   "future_note"),
    "journal":   ("overseer_journal",      ["body"],               "j",   "journal_entry"),
    "narrative": ("temporal_narratives",   ["narrative"],          "nar", "temporal_narrative"),
    "question":  ("open_questions",        ["question", "body"],   "q",   "question"),
    "blindspot": ("known_blindspots",      ["body", "rationale"],  "b",   "blindspot"),
    "human":     ("human_journal_entries", ["text"],               "hj",  "human_journal_entry"),
}

ABSTRACTION_KINDS = {
    "theme", "episode", "pattern", "drift", "future_note", "journal_entry",
    "temporal_narrative", "question", "blindspot", "human_journal_entry",
}

# prefix -> (table, body_columns, title_column|None, kind_label)
PREFIX_TARGETS: dict[str, tuple[str, list[str], str | None, str]] = {}
for _kind, (_table, _cols, _prefix, _label) in SEARCH_TARGETS.items():
    _title = "title" if "title" in _cols else ("name" if "name" in _cols else None)
    _body = _cols[-1] if _cols else "body"
    PREFIX_TARGETS[_prefix] = (_table, _cols, _title, _label)


def title_for(prefix: str) -> str | None:
    t = PREFIX_TARGETS.get(prefix)
    return t[2] if t else None
