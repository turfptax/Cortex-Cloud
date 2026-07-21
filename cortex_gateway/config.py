"""Gateway configuration - all env-driven so the same code runs on a dev
box (against local DB copies) and on the Pi 5 home node (against the
canonical DBs) without edits.

Env vars:
  CORTEX_CORE_PATH   Root of the cortex-core repo. The overseer plugin's
                     corpus/detail engines + cortex_db live here and get
                     added to sys.path. Default: sibling ../cortex-core.
  CORTEX_DB_PATH     Path to cortex.db (relational spine). Default:
                     <CORTEX_CORE_PATH>/data/cortex.db, falling back to the
                     repo's db/cortex_fresh.db for local dev.
  OVERSEER_DB_PATH   Path to overseer.db (interpretive layer). Default:
                     <CORTEX_CORE_PATH>/plugins/overseer/data/overseer.db.
  GATEWAY_HOST       Bind host. Default 127.0.0.1 (Cloudflare Tunnel fronts it).
  GATEWAY_PORT       Bind port. Default 8430.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve()
_GATEWAY_REPO = _HERE.parent.parent          # cortex-gateway/
_WORKSPACE = _GATEWAY_REPO.parent            # C:/dev/ttx/Cortex (sibling repos)


class Settings:
    def __init__(self) -> None:
        core = os.environ.get("CORTEX_CORE_PATH")
        self.cortex_core_path = Path(core) if core else (_WORKSPACE / "cortex-core")

        db = os.environ.get("CORTEX_DB_PATH")
        if db:
            self.cortex_db_path = Path(db)
        else:
            canonical = self.cortex_core_path / "data" / "cortex.db"
            self.cortex_db_path = canonical if canonical.exists() else (
                _WORKSPACE / "db" / "cortex_fresh.db"
            )

        odb = os.environ.get("OVERSEER_DB_PATH")
        self.overseer_db_path = Path(odb) if odb else (
            self.cortex_core_path / "plugins" / "overseer" / "data" / "overseer.db"
        )

        # Canonical store as a SQLAlchemy URL. ONE database holds both the
        # relational spine and the interpretive layer (53 tables).
        #   dev  : sqlite:///<file>
        #   Azure: mssql+pyodbc://USER:PWD@SERVER.database.windows.net:1433/cortex
        #          ?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes
        db_url = os.environ.get("DB_URL")
        if db_url:
            self.db_url = db_url
        else:
            # Default to a single local SQLite file for dev.
            local = os.environ.get("CORTEX_DB_PATH") or str(
                _GATEWAY_REPO / ".devdata" / "cortex_all.db")
            self.db_url = "sqlite:///" + str(local).replace("\\", "/")

        # -- Solo-cloud ATTACH topology (P2, docs/CLOUD_MIGRATION.md) --
        # When GATEWAY_DB_PATH is set (and DB_URL is not; an explicit
        # DB_URL always wins so Azure prod can never be hijacked by a
        # stray env), the gateway opens ITS OWN gateway.db as the main
        # database. It is the sole writer there: tokens, oauth_*,
        # connector_*, sync_row_map, and its own pull_events. The core's
        # cortex.db + overseer.db are ATTACHed strictly read-only
        # (SQLite mode=ro URIs), so the gateway physically cannot write
        # the corpus; corpus writes route through the co-located core
        # over HTTP instead (core_client.py).
        gw_db = os.environ.get("GATEWAY_DB_PATH")
        self.gateway_db_path = Path(gw_db) if gw_db else None
        self.attach_mode = bool(gw_db and not db_url)
        if self.attach_mode:
            p = str(self.gateway_db_path).replace("\\", "/")
            # uri=true opens the main DB with SQLITE_OPEN_URI, which is
            # what makes the mode=ro ATTACH URIs below actually parse as
            # URIs (otherwise SQLite treats them as literal filenames).
            self.db_url = "sqlite:///file:" + p + "?uri=true"

        # Co-located core (the corpus writer) for routed writes. The
        # Basic pair mirrors the core's P0 envs (CORTEX_SERVICE_TOKEN).
        self.core_url = os.environ.get(
            "CORTEX_CORE_URL", "http://127.0.0.1:8420").rstrip("/")
        self.core_username = os.environ.get("CORTEX_HTTP_USERNAME", "cortex")
        self.core_token = os.environ.get("CORTEX_SERVICE_TOKEN", "cortex")

        self.host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
        self.port = int(os.environ.get("GATEWAY_PORT", "8430"))

        # Cloud web UI (Phase A, docs/CLOUD_MIGRATION.md): path to the
        # built Hub SPA. When set, the gateway serves the SPA at / and
        # exposes the /api facade it calls (rest/hub_api.py) plus the
        # /intro page. Empty (default) = no web UI, no facade; a
        # Pi/tunnel gateway keeps its surface unchanged. The facade
        # trusts the Easy-Auth-injected x-ms-client-principal header,
        # which is only unforgeable behind ACA Easy Auth, so ONLY set
        # this on the cloud deployment.
        self.static_dir = os.environ.get("GATEWAY_STATIC_DIR", "").strip()
        self.web_ui = bool(self.static_dir)

        # Web-UI owner allowlist (Entra object ids). The /api facade
        # forwards to the core with the full-privilege service token, so
        # in web-UI mode access must be pinned to the owner's account,
        # not merely "is signed in" (a single-tenant Entra app with
        # appRoleAssignmentRequired=false admits any tenant account).
        # EMPTY = presence-only fallback (logged as a warning at startup
        # when web_ui is on); set GATEWAY_OWNER_OIDS to enforce. Space-,
        # comma-, or newline-separated, case-insensitive.
        raw_owner = os.environ.get("GATEWAY_OWNER_OIDS", "")
        self.owner_oids = frozenset(
            o.lower() for o in raw_owner.replace(",", " ").split() if o)

        # Cloud voice (Phase A3). Keys come from Key Vault secret refs.
        # Empty = that backend is unavailable and the SPA falls back to
        # the browser's on-device speech (TTS) or hides the mic (STT).
        # The Pipecat voice AGENT has no cloud path and stays desktop.
        self.groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.elevenlabs_api_key = os.environ.get(
            "ELEVENLABS_API_KEY", "").strip()
        self.elevenlabs_voice_id = os.environ.get(
            "ELEVENLABS_VOICE_ID", "").strip()

        # Public HTTPS base URL the Cloudflare Tunnel exposes (no trailing
        # slash), e.g. https://cortex.example.com. Used as the OAuth issuer +
        # in discovery metadata. Falls back to the request base URL when unset.
        self.public_url = os.environ.get("GATEWAY_PUBLIC_URL", "").rstrip("/")

        # OAuth consumer-connector flow (oauth.py). DISABLED by default: it is a
        # public token-minting surface and no connector uses it yet (manual
        # bearer tokens only). Set GATEWAY_OAUTH_ENABLED=1 to enable it when
        # wiring a real OAuth connector (then it is Entra-gated + scope-validated).
        self.oauth_enabled = os.environ.get(
            "GATEWAY_OAUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")

        # OAuth 2.1 best practice: access tokens are short-lived, not immortal.
        # OAuth-minted tokens expire after this many seconds (default 24h). With
        # no refresh-token rotation yet, this TTL IS the leak-exposure window, so
        # the default is deliberately short; a connector re-runs the authorize
        # flow to get a fresh one. Raise it once refresh rotation exists. Set
        # GATEWAY_OAUTH_TOKEN_TTL=0 for non-expiring tokens (not recommended).
        # (Pre-shared connector keys are managed separately and unaffected.)
        _default_ttl = 24 * 3600
        try:
            self.oauth_token_ttl = int(
                os.environ.get("GATEWAY_OAUTH_TOKEN_TTL", str(_default_ttl)))
        except ValueError:
            self.oauth_token_ttl = _default_ttl

        # Trusted-client redirect_uri allowlist. When NON-EMPTY, OAuth dynamic
        # registration and the authorize flow accept ONLY these exact https
        # redirect URIs (e.g. the claude.ai / chatgpt.com connector callbacks),
        # which is the primary defense against consent-phishing: an attacker
        # cannot register their own redirect to receive the code. EMPTY (unset)
        # keeps registration open, so a rollout can test real connectors first,
        # observe the exact callbacks they use, then lock down by setting this.
        # Space-, comma-, or newline-separated.
        raw_allow = os.environ.get("GATEWAY_OAUTH_ALLOWED_REDIRECTS", "")
        self.oauth_allowed_redirects = frozenset(
            u for u in raw_allow.replace(",", " ").split() if u)

        # Exhaustive debug tracing. OFF by default. When GATEWAY_DEBUG=1, a
        # middleware logs a structured line for EVERY request (method/path/query/
        # status, whether Easy Auth authenticated it + which identity, client IP,
        # user-agent, timing) and the OAuth token/authorize internals log
        # step-by-step at DEBUG. Toggle via app setting + restart: turn on to
        # capture a failing flow, off once it works, on again to debug later.
        # Debug logs identity claims (PII) and is single-user only, so keep off
        # in steady state.
        self.debug = os.environ.get(
            "GATEWAY_DEBUG", "").strip().lower() in ("1", "true", "yes")

        # OAuth connectors are READ-ONLY by default: `connector:write` is only
        # granted when GATEWAY_OAUTH_ALLOW_WRITE is set. Safer default (a leaked
        # or over-broad connector token can't ingest/poison the corpus), and it
        # is the per-deployment lever for "start read-only, revisit write later".
        self.oauth_allow_write = os.environ.get(
            "GATEWAY_OAUTH_ALLOW_WRITE", "").strip().lower() in ("1", "true", "yes")

        # Allow RFC 8252 loopback redirects (http://127.0.0.1:PORT/... and
        # http://localhost:PORT/...) for native-app clients like Claude Code,
        # matched port-agnostically. Off by default. Safe when on: the code is
        # only ever delivered to the user's own machine (a remote attacker can't
        # intercept a loopback redirect), and PKCE + the Entra-gated consent
        # still apply. Needed because Claude Code uses ephemeral-port http
        # loopback callbacks (claude.ai hosted surfaces use the https callback).
        self.oauth_allow_loopback = os.environ.get(
            "GATEWAY_OAUTH_ALLOW_LOOPBACK", "").strip().lower() in ("1", "true", "yes")

        # INTERIM connector access grant (superseded by the connector_grants DB
        # model, see docs/CONNECTOR_GRANTS_DESIGN.md). A connector reads NOTHING
        # unless its registered redirect host is in this set (default deny). This
        # closes the untagged-corpus leak (security audit 2026-07-12, Finding 1)
        # for any unapproved connector while keeping approved ones (Grok) at full.
        # Keyed by redirect host so it survives the connector's token re-mints.
        raw_full = os.environ.get("GATEWAY_CONNECTOR_FULL_HOSTS", "")
        self.connector_full_hosts = frozenset(
            h.lower() for h in raw_full.replace(",", " ").split() if h)

        # HUB / owner-device clients. A client whose OAuth redirect host is in
        # this set is the owner's own app (the phone/Hub): the token exchange
        # mints the elevated `hub` scope (implies `app`) instead of a connector
        # scope, and the redirect is auto-trusted. Default EMPTY = inert (no
        # client can obtain `hub`), so this NEVER widens scope until the owner
        # explicitly names the phone's redirect host. Security rests on the host
        # being one only the phone app controls (an app-claimed https link).
        raw_hub = os.environ.get("GATEWAY_HUB_REDIRECT_HOSTS", "")
        self.hub_redirect_hosts = frozenset(
            h.lower() for h in raw_hub.replace(",", " ").split() if h)
        # Hub token lifetime (owner device). Default 30 days; 0 = non-expiring.
        try:
            self.hub_token_ttl = int(
                os.environ.get("GATEWAY_HUB_TOKEN_TTL", str(30 * 24 * 3600)))
        except ValueError:
            self.hub_token_ttl = 30 * 24 * 3600


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
