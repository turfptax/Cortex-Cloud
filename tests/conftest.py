"""Shared test fixtures."""
import importlib

import pytest


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    """A fresh Gateway bound to a throwaway SQLite DB, OAuth enabled."""
    db_file = tmp_path / "gw_test.db"
    monkeypatch.setenv("DB_URL", "sqlite:///" + str(db_file).replace("\\", "/"))
    monkeypatch.setenv("GATEWAY_OAUTH_ENABLED", "1")

    from cortex_gateway import config, db, oauth
    config.get_settings.cache_clear()
    db.engine.cache_clear()
    importlib.reload(db)     # rebind the lru_cached engine to the new URL
    db.init_schema()
    # oauth/auth/grants import db by module ref, so the reloaded module is shared.
    return config, db, oauth
