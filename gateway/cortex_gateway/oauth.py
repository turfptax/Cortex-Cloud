"""OAuth 2.1 + PKCE - Phase 2 authorization for consumer-UI connector flows
(claude.ai / ChatGPT custom connectors that won't take a pasted bearer token).

Single-user system: there is exactly one resource owner (Tory), so the consent
screen is a simple approve button rather than a login. Public clients + PKCE
(no client secret). Authorization-code grant, S256 only.

An OAuth-issued access token is just a row in the same `gateway_tokens` table
the bearer path uses, so everything downstream (scopes, tier ceiling, pull
attribution) is identical. Pre-shared bearer tokens remain valid in parallel.

Endpoints:
  GET  /.well-known/oauth-authorization-server   discovery (RFC 8414)
  GET  /.well-known/oauth-protected-resource     resource metadata (RFC 9728)
  POST /oauth/register                           dynamic client reg (RFC 7591, minimal)
  GET  /oauth/authorize                          consent screen
  POST /oauth/authorize                          approve → redirect with code
  POST /oauth/token                              code + verifier → access token
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth, db, grants
from .config import get_settings

router = APIRouter(tags=["oauth"])

_CODE_TTL = 60.0  # seconds
_CONSENT_TTL = 300.0  # seconds - how long a rendered consent screen stays approvable
_ALLOWED_SCOPES = {"connector:read", "connector:write"}

# uvicorn does not attach a handler to the root logger, so INFO records would be
# dropped (only WARNING+ reaches the last-resort handler). Attach one lightweight
# stderr handler to the package logger so BOTH successful (info) and blocked
# (warning) OAuth events surface in the App Service log stream. Idempotent.
_pkg_log = logging.getLogger("cortex_gateway")
if not _pkg_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _pkg_log.addHandler(_h)
# DEBUG surfaces the request trace + OAuth internals when GATEWAY_DEBUG is on.
_pkg_log.setLevel(logging.DEBUG if get_settings().debug else logging.INFO)
log = logging.getLogger("cortex_gateway.oauth")


def _client_ip(request: Request) -> str:
    """Best-effort caller IP for the audit line (Azure fronts the app, so the
    real client is first in X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _audit(event: str, request: Request, outcome: str, **fields) -> None:
    """One structured line per registration / authorize attempt, for security
    visibility. Lightweight: goes to the log stream, no DB write. `outcome` is
    'allowed' or 'blocked'; blocked logs at WARNING so it is easy to filter.
    Never logs secrets (no authorization code, verifier, or token)."""
    rec = {"event": event, "outcome": outcome,
           "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "ip": _client_ip(request), **fields}
    line = json.dumps(rec, separators=(",", ":"), default=str)
    (log.warning if outcome == "blocked" else log.info)("oauth_audit %s", line)


def _require_enabled() -> None:
    """OAuth is a public token-minting surface; 404 unless explicitly enabled."""
    if not get_settings().oauth_enabled:
        raise HTTPException(404, "not found")


def _clean_scope(scope: str) -> str:
    """Keep only allowed connector scopes. Accepts space- OR comma-delimited
    input and returns the RFC 6749 space-delimited form (for the consent screen
    and the token response). Never let admin/app reach a minted token via OAuth.
    Also drops `connector:write` unless the deployment opts into write
    (GATEWAY_OAUTH_ALLOW_WRITE) - connectors are read-only by default. Defaults
    to connector:read. NB: the stored `gateway_tokens.scopes` column is
    comma-delimited, so the token() mint site converts before persisting."""
    parts = (scope or "").replace(",", " ").split()
    keep = [s for s in dict.fromkeys(parts) if s in _ALLOWED_SCOPES]
    if not get_settings().oauth_allow_write:
        keep = [s for s in keep if s != "connector:write"]
    return " ".join(keep) or "connector:read"


def _valid_redirect(uri: str) -> bool:
    """Require an absolute https redirect (no http/custom/javascript: schemes)."""
    try:
        u = urlparse(uri)
    except Exception:
        return False
    return u.scheme == "https" and bool(u.netloc)


def _is_loopback_redirect(uri: str) -> bool:
    """RFC 8252 loopback redirect for native-app clients (Claude Code):
    http://127.0.0.1:PORT/... or http://localhost:PORT/... Gated by config.
    Safe: the code is delivered only to the user's own machine."""
    if not get_settings().oauth_allow_loopback:
        return False
    try:
        u = urlparse(uri)
    except Exception:
        return False
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")


def _is_hub_redirect(uri: str) -> bool:
    """The owner's own app (phone/Hub): redirect host is in GATEWAY_HUB_REDIRECT_HOSTS.
    Such a client is minted the elevated `hub` scope (see token()). Default empty
    => inert. Security rests on the host being one only the phone app controls."""
    hosts = get_settings().hub_redirect_hosts
    if not hosts:
        return False
    try:
        host = urlparse(uri).hostname
    except Exception:
        return False
    return bool(host) and host.lower() in hosts


def _redirects_match(a: str, b: str) -> bool:
    """Exact match, EXCEPT loopback redirects match port-agnostically (RFC 8252
    §7.3: the native app picks an ephemeral port, so the AS ignores it)."""
    if a == b:
        return True
    ua, ub = urlparse(a), urlparse(b)
    return (ua.scheme == ub.scheme == "http"
            and ua.hostname in ("127.0.0.1", "localhost")
            and ua.hostname == ub.hostname and ua.path == ub.path)


def _redirect_allowed(uri: str) -> bool:
    """A redirect must be an allowed callback. When a trusted-client allowlist is
    configured, an https redirect must be an exact member of it (the primary
    consent-phishing defense). Loopback native-app redirects (if enabled) are
    always allowed since their port varies and the code can only reach the
    user's own machine. An empty allowlist means open https registration."""
    if _is_loopback_redirect(uri) or _is_hub_redirect(uri):
        return True
    if not _valid_redirect(uri):
        return False
    allow = get_settings().oauth_allowed_redirects
    return (not allow) or (uri in allow)


def ensure_schema() -> None:
    db.init_schema()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _verify_pkce(verifier: str, challenge: str) -> bool:
    expected = _b64url(hashlib.sha256(verifier.encode()).digest())
    return secrets.compare_digest(expected, challenge)


def _issuer(request: Request) -> str:
    s = get_settings()
    return s.public_url or str(request.base_url).rstrip("/")


# ── Discovery ─────────────────────────────────────────────────────────


@router.get("/.well-known/oauth-authorization-server")
def as_metadata(request: Request):
    base = _issuer(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(_ALLOWED_SCOPES),
        # RFC 9207: the authorization response carries an `iss` so the client
        # can detect an authorization-server mix-up / code-injection swap.
        "authorization_response_iss_parameter_supported": True,
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def resource_metadata(request: Request):
    # Served at BOTH the bare well-known path and the RFC 9728 path-suffixed
    # form (/.well-known/oauth-protected-resource/mcp) that MCP clients derive
    # from the resource identifier; connectors probe the suffixed one.
    base = _issuer(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": sorted(_ALLOWED_SCOPES),
    }


# ── Dynamic client registration (minimal RFC 7591) ────────────────────


@router.post("/oauth/register")
async def register(request: Request):
    _require_enabled()
    ensure_schema()
    body = await request.json()
    client_name = body.get("client_name") or "connector"
    redirect_uris = body.get("redirect_uris") or []
    if not redirect_uris:
        _audit("register", request, "blocked", reason="no_redirect_uris",
               client_name=client_name, redirect_uris=[])
        raise HTTPException(400, "redirect_uris required")
    if not all(_valid_redirect(u) or _is_loopback_redirect(u) for u in redirect_uris):
        _audit("register", request, "blocked", reason="redirect_not_https",
               client_name=client_name, redirect_uris=redirect_uris)
        raise HTTPException(
            400, "redirect_uris must be absolute https URLs "
                 "(or http loopback for native apps)")
    if not all(_redirect_allowed(u) for u in redirect_uris):
        # A trusted-client allowlist is configured and one or more redirect URIs
        # are not on it. Reject rather than register an untrusted callback.
        _audit("register", request, "blocked", reason="redirect_not_allowlisted",
               client_name=client_name, redirect_uris=redirect_uris)
        raise HTTPException(400, "redirect_uri not in the trusted-client allowlist")
    # Dedup: a public client (PKCE, no secret) that re-registers with the same
    # identity (name + redirect set) reuses its client_id instead of spawning a
    # new one. Without this, every reconnect minted a fresh client_id, so one
    # service showed up as many pending connections that each needed approval.
    # Prefer a client_id that already has an active grant, so a re-registration
    # lands on the connection the owner already approved.
    stored_redirects = "\n".join(redirect_uris)
    dup = db.fetchall(
        "SELECT c.client_id FROM oauth_clients c "
        "LEFT JOIN connector_grants g ON g.client_id = c.client_id "
        "WHERE c.client_name = :n AND c.redirect_uris = :r "
        "ORDER BY (CASE WHEN g.status = 'active' THEN 0 ELSE 1 END), c.client_id",
        {"n": client_name, "r": stored_redirects})
    if dup:
        client_id = dup[0]["client_id"]
    else:
        client_id = "cli_" + secrets.token_urlsafe(16)
        db.insert("oauth_clients", {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": stored_redirects,
        })
    _audit("register", request, "allowed", client_id=client_id,
           client_name=client_name, redirect_uris=redirect_uris)
    return JSONResponse({
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    }, status_code=201)


# ── Authorization (consent) ───────────────────────────────────────────


def _known_redirect(client_id: str, redirect_uri: str) -> bool:
    row = db.fetchone("SELECT redirect_uris FROM oauth_clients WHERE client_id = :cid",
                      {"cid": client_id})
    if not row:
        return False
    return any(_redirects_match(redirect_uri, r)
               for r in (row["redirect_uris"] or "").splitlines())


@router.get("/oauth/authorize", response_class=HTMLResponse)
def authorize(request: Request, response_type: str = "code",
              client_id: str = "", redirect_uri: str = "",
              code_challenge: str = "", code_challenge_method: str = "S256",
              scope: str = "connector:read", state: str = "", consent: str = ""):
    """Consent endpoint, GET-only, in two modes:

      1. render - no `consent` param: validate the authorize request, mint a
         single-use consent nonce bound to it, show the Approve screen.
      2. approve - `consent=<nonce>`: the human clicked Approve. Redeem the
         nonce, issue the authorization code, redirect back to the connector.

    Approval is a GET (not a POST) deliberately: Azure Easy Auth 403s the
    authenticated POST inside a connector's popup, but passes GETs through. The
    single-use, request-bound nonce keeps the state-changing GET safe from
    forgery/replay (and is the consent-nonce CSRF hardening we wanted anyway)."""
    _require_enabled()
    ensure_schema()
    if consent:
        return _finish_consent(request, consent)
    if response_type != "code":
        raise HTTPException(400, "only response_type=code supported")
    if code_challenge_method != "S256":
        raise HTTPException(400, "only S256 PKCE supported")
    if not (client_id and redirect_uri and code_challenge):
        raise HTTPException(400, "client_id, redirect_uri and code_challenge required")
    if not _known_redirect(client_id, redirect_uri):
        _audit("authorize", request, "blocked", reason="unknown_redirect",
               client_id=client_id, redirect_uri=redirect_uri, scope=scope)
        raise HTTPException(400, "unknown client_id / redirect_uri")
    if not _redirect_allowed(redirect_uri):
        # Grandfathered client (registered before the allowlist) or a non-listed
        # redirect: refuse to render consent for an untrusted callback.
        _audit("authorize", request, "blocked", reason="redirect_not_allowlisted",
               client_id=client_id, redirect_uri=redirect_uri, scope=scope)
        raise HTTPException(400, "redirect_uri not in the trusted-client allowlist")
    requested_scope = scope
    scope = _clean_scope(scope)   # never persist admin/app via OAuth
    # Consent screen shown: log requested vs granted scope so a stripped
    # admin/app escalation attempt is visible even before approval.
    _audit("authorize", request, "prompt", client_id=client_id,
           redirect_uri=redirect_uri, requested_scope=requested_scope,
           granted_scope=scope)
    nonce = "cnf_" + secrets.token_urlsafe(24)
    db.insert("oauth_consent", {
        "nonce": nonce, "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": code_challenge, "scope": scope, "state": state,
        "expires_at": time.time() + _CONSENT_TTL,
    })
    e = html.escape          # escape every reflected value into the HTML
    approve_url = "/oauth/authorize?consent=" + nonce   # nonce is url-safe
    page = f"""
    <html><body style="font-family:sans-serif;max-width:560px;margin:60px auto">
      <h2>Authorize connector access to Cortex</h2>
      <p>A connector (<b>{e(client_id)}</b>) is requesting access to your Cortex
         corpus with scope <code>{e(scope)}</code>.</p>
      <p>Only approve if you started this from a connector you trust.</p>
      <p><a href="{e(approve_url)}" style="display:inline-block;padding:10px 18px;
         font-size:16px;background:#111;color:#fff;text-decoration:none;
         border-radius:6px">Approve</a></p>
    </body></html>
    """
    return HTMLResponse(page, headers={
        "Content-Security-Policy":
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'"})


def _finish_consent(request: Request, nonce: str) -> RedirectResponse:
    """Redeem a consent nonce (single-use) and issue the authorization code."""
    row = db.fetchone("SELECT * FROM oauth_consent WHERE nonce = :n", {"n": nonce})
    if not row or row["used"] or row["expires_at"] < time.time():
        raise HTTPException(400, "consent expired or already used")
    # Atomic single-use: only the redeemer that flips used 0->1 may proceed.
    if db.execute_write("UPDATE oauth_consent SET used = 1 "
                        "WHERE nonce = :n AND used = 0", {"n": nonce}) != 1:
        raise HTTPException(400, "consent already used")
    client_id, redirect_uri = row["client_id"], row["redirect_uri"]
    scope = _clean_scope(row["scope"] or "connector:read")   # never admin/app
    _audit("authorize", request, "allowed", client_id=client_id,
           redirect_uri=redirect_uri, granted_scope=scope)
    code = "code_" + secrets.token_urlsafe(24)
    db.insert("oauth_codes", {
        "code": code, "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": row["code_challenge"], "scope": scope,
        "expires_at": time.time() + _CODE_TTL,
    })
    sep = "&" if "?" in redirect_uri else "?"
    # RFC 9207: echo the issuer so the client can reject a mixed-up AS.
    target = f"{redirect_uri}{sep}code={code}&iss={quote(_issuer(request), safe='')}"
    if row["state"]:
        target += f"&state={quote(row['state'], safe='')}"
    return RedirectResponse(target, status_code=302)


# ── Token exchange ────────────────────────────────────────────────────


def _record_connection(request: Request, client_id: str, scope: str,
                       max_tier: str) -> None:
    """Durable record of a successful connector authentication, written into the
    canonical Cortex store (connector_connections) so there is a clean history
    of what connected - not just an Azure log line. Best-effort: a logging
    failure must never break token issuance."""
    try:
        row = db.fetchone("SELECT client_name FROM oauth_clients "
                          "WHERE client_id = :c", {"c": client_id})
        name = (row or {}).get("client_name") or client_id
        db.insert("connector_connections", {
            "client_id": client_id, "name": name, "kind": "oauth",
            "scope": scope, "max_tier": max_tier,
            "source_ip": _client_ip(request),
        })
        log.info("connector_connected %s", json.dumps(
            {"client_id": client_id, "name": name, "scope": scope,
             "max_tier": max_tier}, separators=(",", ":")))
    except Exception as e:   # noqa: BLE001 - never fail the token exchange
        log.warning("connector_connections insert failed: %s", e)


@router.post("/oauth/token")
def token(request: Request, grant_type: str = Form(...), code: str = Form(...),
          redirect_uri: str = Form(...), client_id: str = Form(...),
          code_verifier: str = Form(...)):
    _require_enabled()
    ensure_schema()
    # Debug (secret-free): explains WHY a token exchange fails. Never logs the
    # code or verifier, only their shape/outcome.
    log.debug("token exchange: grant_type=%s client_id=%s redirect=%s "
              "code_present=%s verifier_len=%s", grant_type, client_id,
              redirect_uri, bool(code), len(code_verifier or ""))
    if grant_type != "authorization_code":
        raise HTTPException(400, "unsupported_grant_type")
    row = db.fetchone("SELECT * FROM oauth_codes WHERE code = :code", {"code": code})
    if not row or row["used"]:
        log.debug("token invalid_grant: code not found or already used "
                  "(found=%s, used=%s)", bool(row), row and row.get("used"))
        raise HTTPException(400, "invalid_grant")
    if row["expires_at"] < time.time():
        log.debug("token invalid_grant: code expired %.1fs ago",
                  time.time() - row["expires_at"])
        raise HTTPException(400, "invalid_grant: code expired")
    if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
        log.debug("token invalid_grant: client/redirect mismatch "
                  "(code client=%s redirect=%s)", row["client_id"], row["redirect_uri"])
        raise HTTPException(400, "invalid_grant: client/redirect mismatch")
    if not _verify_pkce(code_verifier, row["code_challenge"]):
        log.debug("token invalid_grant: PKCE S256 mismatch for client_id=%s", client_id)
        raise HTTPException(400, "invalid_grant: PKCE verification failed")
    log.debug("token exchange: all checks passed for client_id=%s, minting", client_id)
    # Atomic single-use consumption: only the transaction that flips used 0->1
    # is allowed to mint. Guards against the code-replay race under scale-out
    # (two concurrent /oauth/token with one code can no longer both succeed).
    if db.execute_write(
            "UPDATE oauth_codes SET used = 1 WHERE code = :code AND used = 0",
            {"code": code}) != 1:
        raise HTTPException(400, "invalid_grant: code already used")
    settings = get_settings()
    # The owner's own app (redirect host in GATEWAY_HUB_REDIRECT_HOSTS) is minted
    # the elevated `hub` scope (implies `app`: full REST + connection management),
    # NOT a connector scope. Everyone else is capped to connector scopes, so a
    # self-registering connector can never obtain `hub`. Default-inert (no hub
    # hosts configured).
    if _is_hub_redirect(redirect_uri):
        scope, kind, max_tier, ttl = "hub", "hub", "restricted", settings.hub_token_ttl
        name = f"hub:{client_id}"
    else:
        scope = _clean_scope(row["scope"] or "connector:read")
        kind, max_tier, ttl = "oauth", "internal", settings.oauth_token_ttl
        name = f"oauth:{client_id}"
    expires_at = None
    if ttl > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    # gateway_tokens.scopes is comma-delimited (auth._lookup splits on it);
    # the OAuth response scope stays space-delimited per RFC 6749.
    access = auth.mint(name=name, scopes=scope.replace(" ", ","),
                       max_tier=max_tier, kind=kind, expires_at=expires_at,
                       client_id=client_id)
    # Connectors: record + upsert a default-deny access grant. The hub (owner
    # device) is neither a connector nor grant-gated, so it is skipped here.
    if kind != "hub":
        _record_connection(request, client_id, scope, max_tier)
        grants.upsert_on_connect(client_id, "", urlparse(redirect_uri).hostname or "")
    body = {"access_token": access, "token_type": "Bearer", "scope": scope}
    if ttl > 0:
        body["expires_in"] = ttl
    return body
