"""Search + token maps, ported from cortex-core (corpus.py / detail.py) so the
Gateway is self-contained and dialect-portable - no SQLite-only dependency on
the overseer plugin at runtime.

SEARCH_TARGETS: kind_key -> (table, body_columns, token_prefix, kind_label)
"""
from __future__ import annotations

SEARCH_TARGETS: dict[str, tuple[str, list[str], str, str]] = {
    "gist":       ("summaries_gist",        ["body"],               "g",   "gist"),
    "theme":      ("summaries_theme",       ["title", "body"],      "t",   "theme"),
    "episode":    ("summaries_episode",     ["title", "body"],      "e",   "episode"),
    "pattern":    ("patterns",              ["name", "body"],       "p",   "pattern"),
    "drift":      ("drift_observations",    ["body", "direction"],  "d",   "drift"),
    # The OVERSEER's memos to its successors, not the owner's notes. This key
    # used to be "note", which meant an agent that wrote a note with
    # cortex_ingest and then searched kinds="note" got the AI's diary back and
    # concluded its write had failed. See "user_note" below for the owner's own
    # captures. The n: prefix is unchanged so tokens already in circulation
    # keep resolving.
    "future_note": ("future_overseer_notes", ["body"],              "n",   "future_note"),
    "journal":    ("overseer_journal",      ["body"],               "j",   "journal_entry"),
    "narrative":  ("temporal_narratives",   ["narrative"],          "nar", "temporal_narrative"),
    "question":   ("open_questions",        ["question", "body"],   "q",   "question"),
    "blindspot":  ("known_blindspots",      ["body", "rationale"],  "b",   "blindspot"),
    "human":      ("human_journal_entries", ["text"],               "hj",  "human_journal_entry"),
    # The owner's own notes (cortex.db). Every other target here is something
    # an AI wrote; this one and "human" are the two the owner authored. It was
    # missing entirely until 2026-08-04, which left the highest-value rows in
    # the corpus unreachable by any connector tool, including the one that
    # writes them.
    "user_note":  ("notes",                 ["content"],            "un",  "user_note"),
}

# What an agent types -> what it means. "note" resolves to the OWNER's notes
# because that is what the word means to everyone except this codebase; the
# AI's memos are reachable under their real name.
KIND_ALIASES: dict[str, str] = {
    "note": "user_note",
    "notes": "user_note",
    "user_notes": "user_note",
    "future": "future_note",
    "future_overseer_note": "future_note",
}


def resolve_kind(key: str) -> str:
    """Map a caller-supplied kind key through the alias table."""
    k = (key or "").strip().lower()
    return KIND_ALIASES.get(k, k)


# Interpretation the AI produced ON TOP of raw material. user_note is
# deliberately absent: a note is raw owner input, the thing interpretations are
# built from, so it belongs in its own layer rather than among abstractions.
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
