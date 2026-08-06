"""A plugin that brings its own handle must not get one built for it.

Added 2026-08-06 with M3. The plugin loader derives each plugin's database
path from the plugin NAME and opens it in _build_api, which runs before the
plugin's on_load reads any config. For a plugin that has been moved onto the
shared corpus that is not merely wasteful, it is destructive in a way
nothing reports:

  - the file is recreated on every boot regardless of env or config, so
    retiring it by deleting it or unsetting a variable does not stick
  - opening it writes the full schema into it, ~80 empty tables
  - SQLite resolves `main` before attached schemas, so every unqualified
    read then finds the empty copy and never the migrated rows

No exception, no log line, no failing request. The corpus simply reads as
wiped. `[capabilities] db = false` is the opt-out, and this pins it.

Run: python scripts/test_plugin_db_optout.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

import plugins_runtime  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def _manifest(tmp, name, caps):
    folder = Path(tmp) / name
    folder.mkdir(parents=True, exist_ok=True)
    return plugins_runtime.PluginManifest(
        name=name, version="0.1.0", description="", enabled=True,
        entrypoint=name, folder=folder, capabilities=caps, llm={},
        hooks=[], config={}, dependencies={})


def main():
    tmp = tempfile.mkdtemp()
    # _build_api is the unit under test; give it the collaborators it
    # reads rather than standing up a whole registry.
    reg = plugins_runtime.PluginRegistry.__new__(
        plugins_runtime.PluginRegistry)
    reg._sound_manager = None
    reg._battery = None
    reg._cortex_db_path = str(Path(tmp) / "cortex.db")

    print("\nscenario: db = false builds nothing and creates no file")
    m = _manifest(tmp, "borrower", {"db": False})
    api = reg._build_api(m)
    check("api.db is None", api.db is None, repr(api.db))
    on_disk = list((Path(tmp) / "borrower" / "data").glob("*.db")) \
        if (Path(tmp) / "borrower" / "data").is_dir() else []
    check("no database file was created", not on_disk, str(on_disk))

    print("\nscenario: the default is unchanged for every other plugin")
    m2 = _manifest(tmp, "owner", {"db": True})
    api2 = reg._build_api(m2)
    check("api.db is built when db is true", api2.db is not None)

    m3 = _manifest(tmp, "unstated", {})
    api3 = reg._build_api(m3)
    check("api.db is built when the capability is absent",
          api3.db is not None)

    for a in (api2, api3):
        try:
            a.db.close()
        except Exception:
            pass

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all plugin-db opt-out checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
