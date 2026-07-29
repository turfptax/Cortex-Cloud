"""Response security headers, and the one that must NOT be hardened further.

The Referrer-Policy assertion here is a regression guard with real history: it
was `no-referrer`, which looks like the strictest and therefore best choice, and
it silently took down every authenticated write in the web Hub.

Azure's platform auth middleware (the `http-auth` sidecar) runs its own CSRF
check on state-changing methods and validates the REFERER header. `no-referrer`
strips Referer from same-origin requests too, so the middleware saw an empty
referer and rejected every POST before it reached this app, with an empty body
and no access-log line:

    MiddlewareWarning "Cross-site request forgery detected for user '...'
    from referer ''!"   403, SubStatusCode 60

GETs were unaffected (no CSRF check), which is why the Hub looked healthy while
nothing could be saved. Do not "improve" this back to `no-referrer`.
"""
def _headers(gw):
    """Importing cortex_gateway.app runs create_app() at module scope, which
    needs a database, so take the `gw` fixture and import lazily (the same
    pattern test_oauth.py uses)."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from cortex_gateway.app import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    with TestClient(app) as c:
        return c.get("/ping").headers


def test_referrer_policy_still_sends_same_origin_referer(gw):
    """The platform CSRF check needs a Referer on our own requests."""
    assert _headers(gw)["referrer-policy"] == "same-origin"


def test_referrer_policy_is_never_no_referrer(gw):
    """Explicit: `no-referrer` breaks every authenticated POST. See module docstring."""
    assert _headers(gw)["referrer-policy"] != "no-referrer"


def test_referrer_policy_does_not_leak_cross_origin(gw):
    """`same-origin` was chosen over `strict-origin-when-cross-origin` because it
    still sends NOTHING to third parties, keeping the original privacy intent."""
    policy = _headers(gw)["referrer-policy"]
    assert policy in {"same-origin", "strict-origin"}, (
        f"{policy} may leak referrer data to third parties")


def test_other_hardening_headers_intact(gw):
    h = _headers(gw)
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert "max-age=" in h["strict-transport-security"]
