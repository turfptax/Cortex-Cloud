"""FastAPI application factory - mounts REST (/v1) + Streamable-HTTP MCP (/mcp)
into one process, with bearer auth + rate limiting on both surfaces and an
OAuth 2.1 + PKCE authorization server for consumer-UI connectors.

MCP auth: a Starlette middleware on the mounted MCP app validates the bearer,
requires a connector scope, and stashes the principal in the contextvar the
MCP tools read.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from . import auth, corpus_service, db, grants, mcp_server, oauth
from .config import get_settings
from .core_client import CoreWriteError
from .ratelimit import limiter
from .rest import admin as rest_admin
from .rest import connections as rest_connections
from .rest import core_proxy as rest_core_proxy
from .rest import corpus as rest_corpus
from .rest import ops as rest_ops
from .rest import relational as rest_relational
from .rest import sync as rest_sync

_trace_log = logging.getLogger("cortex_gateway.trace")

# Human/browser surfaces that require an interactive Azure (Entra) login.
# Everything else is a machine surface authenticated by bearer tokens / the
# app's own OAuth. Enforced here (deterministic) rather than via Easy Auth's
# opaque excludedPaths matching. Azure App Service Easy Auth runs in
# AllowAnonymous mode and injects the verified `x-ms-client-principal` header
# for logged-in users; we redirect to the platform login when it's absent.
_HUMAN_PATHS = frozenset({
    "/", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json",
    # The OAuth consent screen requires an interactive Entra login, so an
    # anonymous caller cannot mint an authorization code. The machine token
    # exchange (/oauth/token) stays bearer/PKCE and is safe because no code is
    # issued without this human login.
    "/oauth/authorize",
})

# Extra human paths that only exist in web-UI mode: the SPA entry point
# and the copy-context page. Gated on web_ui so a Pi/tunnel deployment
# (no SPA, no /intro) keeps its exact pre-change surface - otherwise
# these would 302 to a /.auth/login/aad path that does not exist behind
# the tunnel. SPA /assets/* stay public: the bundle is built from a
# public repo and carries no data; every data call it makes hits the
# /api facade, which 401s without a principal.
_WEBUI_HUMAN_PATHS = frozenset({"/index.html", "/intro"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth response headers. HTTPS is forced at the platform
    (httpsOnly), so HSTS is safe to assert. The rest harden the browser-facing
    surfaces (/docs etc.) against MIME-sniffing, framing, and referrer leaks."""

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp


class HumanLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        human = path in _HUMAN_PATHS or (
            get_settings().web_ui and path in _WEBUI_HUMAN_PATHS)
        if human:
            # Easy Auth strips this header from inbound requests and only sets
            # it for authenticated users, so its presence can't be spoofed.
            if not request.headers.get("x-ms-client-principal"):
                # Preserve the ORIGINAL query string across the login round-trip.
                # /oauth/authorize carries client_id/redirect_uri/code_challenge
                # in the query; without this they are lost on return from Entra
                # and the consent endpoint 422s. safe="" so the ?/&/= inside the
                # redirect target are encoded as one opaque value for Easy Auth.
                dest = request.url.path
                if request.url.query:
                    dest += "?" + request.url.query
                target = ("/.auth/login/aad?post_login_redirect_uri="
                          + quote(dest, safe=""))
                return RedirectResponse(target, status_code=302)
        return await call_next(request)


def _source_ip(request: Request) -> str:
    """Real client IP. Azure fronts the app, so the caller is first in
    X-Forwarded-For; fall back to the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _client_key(request: Request) -> tuple[str, bool]:
    """Rate-limit key + whether the caller is authenticated. The anon key uses
    the forwarded client IP, NOT request.client.host: behind Azure the socket
    peer is the platform proxy (169.254.x.x), so keying on it collapses every
    anonymous caller into one shared bucket - which throttled the OAuth
    discovery bootstrap for all connectors at once."""
    authz = request.headers.get("authorization") or ""
    if authz.lower().startswith("bearer "):
        return "tok:" + auth.hash_token(authz.split(" ", 1)[1].strip())[:16], True
    # Web-UI mode ONLY: an Entra-authenticated browser session (Easy Auth
    # injects the principal, strips it from inbound requests) is an
    # authenticated caller. Without this the SPA's page-load burst of
    # /api fetches lands in the tight anonymous bucket and 429s. Gated on
    # web_ui: the header is only unforgeable behind ACA Easy Auth, so on
    # a Pi/tunnel deployment (nothing strips it) trusting it would let an
    # anonymous caller send the header and jump into the generous bucket.
    if get_settings().web_ui and request.headers.get("x-ms-client-principal"):
        return "human:" + _source_ip(request), True
    return "ip:" + _source_ip(request), False


# Paths exempt from rate limiting: liveness, and the public OAuth discovery
# documents. Discovery is static, cheap, and safe to serve freely; an
# OAuth-capable connector (Grok/Claude/ChatGPT) fetches several in a burst
# during the handshake, so limiting them breaks the connection with no benefit.
def _rate_exempt(path: str) -> bool:
    return path == "/health" or path.startswith("/.well-known/")


def _decode_easyauth_identity(header_val: str) -> dict:
    """Decode the Easy Auth `x-ms-client-principal` (base64 JSON) down to the
    identity claims, for DEBUG tracing only. Reveals WHICH account Easy Auth
    authenticated (idp / name / upn / tenant) - the key signal for diagnosing
    'wrong Entra id' 403s. Never called unless GATEWAY_DEBUG is on."""
    try:
        data = json.loads(base64.b64decode(header_val))
    except Exception:
        return {"decode": "failed"}
    claims = {c.get("typ"): c.get("val") for c in data.get("claims", [])}

    def pick(*keys: str):
        for k in keys:
            if claims.get(k):
                return claims[k]
        return None

    return {
        "idp": data.get("auth_typ"),
        "name": pick("name",
                     "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"),
        "upn": pick("preferred_username",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"),
        "tid": pick("http://schemas.microsoft.com/identity/claims/tenantid", "tid"),
    }


class DebugTraceMiddleware(BaseHTTPMiddleware):
    """Exhaustive per-request trace, gated by GATEWAY_DEBUG. Outermost, so it
    sees the FINAL status (incl. rate-limit 429s and login 302s). Logs one
    `oauth_trace` line: method, path, query, status, whether Easy Auth injected
    a principal + the decoded identity, client IP, user-agent, and duration.
    Off = passthrough (near-zero cost). Never logs secrets (no code/token/verifier
    are in request lines; the token exchange body is a POST, not traced here)."""

    async def dispatch(self, request: Request, call_next):
        if not get_settings().debug:
            return await call_next(request)
        start = time.monotonic()
        resp = await call_next(request)
        principal = request.headers.get("x-ms-client-principal")
        rec = {
            "trace": "req",
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
            "status": resp.status_code,
            "authed": bool(principal),
            "identity": _decode_easyauth_identity(principal) if principal else None,
            "ip": _source_ip(request),
            "ua": (request.headers.get("user-agent") or "")[:80],
            "ms": round((time.monotonic() - start) * 1000),
        }
        _trace_log.info("oauth_trace %s",
                        json.dumps(rec, default=str, separators=(",", ":")))
        return resp


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _rate_exempt(request.url.path):
            return await call_next(request)
        key, authed = _client_key(request)
        ok, retry = limiter.check(key, authenticated=authed)
        if not ok:
            return JSONResponse(
                {"error": "rate_limited", "retry_after": round(retry, 2)},
                status_code=429, headers={"Retry-After": str(int(retry) + 1)})
        # Bind source IP for pull-event audit logging (read by _record_pull).
        tok = corpus_service.source_ip_var.set(_source_ip(request))
        try:
            return await call_next(request)
        finally:
            corpus_service.source_ip_var.reset(tok)


class MCPBearerMiddleware(BaseHTTPMiddleware):
    """Validate the bearer token on every MCP request and bind the principal
    to the contextvar. Connector scope required (connector:read or :write).
    On 401, advertise the protected-resource metadata so OAuth-capable
    connectors can discover the authorization server (RFC 9728)."""

    def __init__(self, app, public_url: str = "") -> None:
        super().__init__(app)
        self._public_url = public_url

    async def dispatch(self, request: Request, call_next):
        authz = request.headers.get("authorization")
        base = self._public_url or str(request.base_url).rstrip("/")
        # RFC 9728 derives the metadata URL from the resource path, so a
        # spec-compliant client looks under /.well-known/oauth-protected-resource/mcp.
        # Advertise that; the un-suffixed path is also served for compatibility.
        challenge = (
            'Bearer resource_metadata='
            f'"{base}/.well-known/oauth-protected-resource/mcp"')
        try:
            principal = auth.principal_from_bearer(authz)
        except Exception as exc:  # HTTPException from auth
            detail = getattr(exc, "detail", "unauthorized")
            return JSONResponse({"error": detail}, status_code=401,
                                headers={"WWW-Authenticate": challenge})
        if not principal.has("connector:read"):
            return JSONResponse(
                {"error": "token lacks connector scope"}, status_code=403)
        token = mcp_server.current_principal.set(principal)
        try:
            return await call_next(request)
        finally:
            mcp_server.current_principal.reset(token)


def create_app() -> FastAPI:
    settings = get_settings()
    db.init_schema()          # create Gateway-owned tables (portable DDL)
    try:
        grants.migrate()      # backfill connector grants (grandfathers seed hosts)
    except Exception:         # non-fatal: grants are created lazily on next mint
        logging.getLogger("cortex_gateway").warning(
            "connector-grant migrate failed at startup", exc_info=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Run the MCP session manager for the lifetime of the app.
        async with mcp_server.mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Cortex Gateway",
        version="0.1.0",
        description="REST (phone app) + Streamable-HTTP MCP (AI connectors) "
                    "over the canonical Cortex corpus.",
        lifespan=lifespan,
    )

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(HumanLoginMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(DebugTraceMiddleware)   # outermost: final status + identity

    # ATTACH mode routes corpus writes through the co-located core; a
    # down/refusing core surfaces as a clean 502 (the phone queues and
    # retries) instead of an opaque 500. The DETAIL stays server-side:
    # CoreWriteError text can carry core-internal protocol/schema
    # strings, which must not reach connectors (review finding).
    @app.exception_handler(CoreWriteError)
    async def _core_write_error(request: Request, exc: CoreWriteError):
        logging.getLogger("cortex_gateway").warning(
            "routed corpus write failed (%s %s): %s",
            request.method, request.url.path, exc)
        return JSONResponse(
            {"error": "corpus write failed: core unavailable or "
                      "rejected the write"},
            status_code=502)

    @app.get("/health")
    def health():
        # Report the DB dialect, not the full URL (it may carry credentials).
        return {
            "ok": True,
            "service": "cortex-gateway",
            "db_dialect": db.engine().dialect.name,
        }

    # REST surface (phone app) + admin (connector keys) + OAuth auth server.
    app.include_router(rest_relational.router)
    app.include_router(rest_corpus.router)
    app.include_router(rest_sync.router)
    app.include_router(rest_ops.router)      # /ops/tick (cloud heartbeat)
    app.include_router(rest_core_proxy.router)  # /core/* (Hub + MCP, P5)
    app.include_router(rest_connections.router)   # /v1/connections (grant mgmt)
    app.include_router(rest_admin.router)
    app.include_router(oauth.router)

    # Web-UI mode (Phase A): the /api facade + /intro page the SPA uses.
    # Only in web-UI mode: the facade trusts the Easy Auth principal
    # header, which is only unforgeable behind ACA Easy Auth (it strips
    # the header from inbound requests). A Pi/tunnel deployment must not
    # expose it. Registered BEFORE the /mcp and / mounts so its routes
    # win route matching.
    if settings.web_ui:
        from .rest import hub_api as rest_hub_api
        from .rest import intro_page as rest_intro_page
        app.include_router(rest_hub_api.router)
        app.include_router(rest_intro_page.router)
        if not settings.owner_oids:
            logging.getLogger("cortex_gateway").warning(
                "web UI is on but GATEWAY_OWNER_OIDS is unset: the /api "
                "surface is gated on Entra login PRESENCE only, so any "
                "account in the Entra tenant can read+write the corpus. "
                "Set GATEWAY_OWNER_OIDS to the owner's object id.")

    # MCP surface (connectors), mounted with its own bearer middleware.
    mcp_app = mcp_server.mcp.streamable_http_app()
    mcp_app.add_middleware(MCPBearerMiddleware, public_url=settings.public_url)

    # Connectors are handed the resource URL "/mcp" (no trailing slash, per
    # the protected-resource discovery doc), but the mounted MCP app lives at
    # "/mcp/". A bare "/mcp" does not match the mount's inner routes and, in
    # web-UI mode, falls through to the SPA static mount below and 404s (which
    # a connector reports as "no MCP server found at the URL"). Redirect it to
    # the canonical "/mcp/" using the public https URL so ACA's TLS
    # termination cannot downgrade the redirect to http. 307 preserves the
    # method and body (MCP posts JSON-RPC). Registered before the mount so it
    # wins the bare-path match; "/mcp/" still falls straight to the mount.
    @app.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"],
                   include_in_schema=False)
    async def _mcp_slash_redirect(request: Request):
        base = settings.public_url or str(request.base_url).rstrip("/")
        return RedirectResponse(f"{base}/mcp/", status_code=307)

    app.mount("/mcp", mcp_app)

    # Web-UI mode: serve the built Hub SPA at /. Mounted LAST so every
    # API route above wins; "/" itself is Entra-gated by
    # HumanLoginMiddleware (_HUMAN_PATHS), and the SPA routes by URL
    # hash so html=True needs no history fallback.
    if settings.web_ui:
        static_path = Path(settings.static_dir)
        if static_path.is_dir():
            app.mount("/", StaticFiles(directory=str(static_path), html=True),
                      name="hub")
        else:
            logging.getLogger("cortex_gateway").warning(
                "GATEWAY_STATIC_DIR set but not a directory: %s",
                settings.static_dir)

    return app


app = create_app()
