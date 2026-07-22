# Cortex Plugins

This directory holds plugins discovered at boot by `src/plugins_runtime.py`.

Each plugin is a self-contained subdirectory:

```
plugins/
└── <name>/
    ├── plugin.toml       # metadata + capabilities (required)
    ├── __init__.py       # def register(api) -> Plugin (required)
    ├── data/             # plugin-owned SQLite + state
    ├── assets/           # plugin-owned media
    └── tests/
```

See `../src/plugin_api.py` for the plugin interface (the `Plugin` and
`PluginAPI` contract).

Discovery is filesystem-driven: a plugin is loaded if and only if its
directory contains a `plugin.toml` with `enabled = true`. Disable a
plugin by flipping that flag or removing the folder.

v0 status: discovery AND loading are live. `src/plugins_runtime.py`
`discover_and_load()` scans this directory, imports each enabled plugin,
and calls its `register(api)` to wire it into the runtime.
