"""CLI for Gateway tokens + connector keys.

App / OAuth / admin tokens:
  python -m cortex_gateway.tokens_cli mint --name phone --scopes app --max-tier restricted
  python -m cortex_gateway.tokens_cli admin-key --name tory-admin
  python -m cortex_gateway.tokens_cli list
  python -m cortex_gateway.tokens_cli revoke --id 3

Connector keys (for external AIs - Grok, ChatGPT, Claude - via Bearer):
  python -m cortex_gateway.tokens_cli connector create --name grok --scope connector:read
  python -m cortex_gateway.tokens_cli connector create --name chatgpt-work \
      --scope connector:read --max-tier internal --categories work
  python -m cortex_gateway.tokens_cli connector list
  python -m cortex_gateway.tokens_cli connector revoke --id 4
  python -m cortex_gateway.tokens_cli connector rotate --id 4

Raw keys are printed ONCE on create/rotate; only the hash is stored.
"""
from __future__ import annotations

import argparse
import sys

from . import auth, connectors, db


# ── generic tokens ────────────────────────────────────────────────────


def _cmd_mint(a: argparse.Namespace) -> int:
    raw = auth.mint(a.name, a.scopes, a.max_tier, a.categories, kind="app")
    print(f"Token '{a.name}' created - copy it now (not shown again):\n\n  {raw}\n")
    print(f"  scopes={a.scopes}  max_tier={a.max_tier}  categories={a.categories or '-'}")
    return 0


def _cmd_admin_key(a: argparse.Namespace) -> int:
    raw = auth.mint(a.name, "admin", "restricted", kind="admin",
                    note="admin key for connector management")
    print(f"Admin key '{a.name}' created - copy it now (not shown again):\n\n  {raw}\n")
    print("  Use it as a Bearer token against /admin/connectors.")
    return 0


def _cmd_list(_a: argparse.Namespace) -> int:
    auth.ensure_schema()
    rows = db.fetchall(
        "SELECT id, name, kind, key_prefix, scopes, max_tier, category_filter, "
        "created_at, last_used_at, revoked_at FROM gateway_tokens ORDER BY id")
    if not rows:
        print("(no tokens)")
        return 0
    for r in rows:
        state = "REVOKED" if r.get("revoked_at") else "active"
        print(f"#{r['id']:<3} [{r.get('kind') or '?':<9}] {r['name']:<22} [{state}] "
              f"scopes={r['scopes']} max_tier={r['max_tier']} "
              f"cats={r.get('category_filter') or '-'} "
              f"prefix={r.get('key_prefix') or '-'} "
              f"last_used={r.get('last_used_at') or 'never'}")
    return 0


def _cmd_revoke(a: argparse.Namespace) -> int:
    auth.ensure_schema()
    db.execute("UPDATE gateway_tokens SET revoked_at = CURRENT_TIMESTAMP "
               "WHERE id = :id", {"id": a.id})
    print(f"Token #{a.id} revoked.")
    return 0


# ── connector keys ────────────────────────────────────────────────────


def _cmd_conn_create(a: argparse.Namespace) -> int:
    try:
        raw = connectors.create(a.name, scope=a.scope, max_tier=a.max_tier,
                                categories=a.categories, note=a.note,
                                expires_at=a.expires_at)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Connector key '{a.name}' created - copy it now (not shown again):\n\n  {raw}\n")
    print(f"  scope={a.scope}  max_tier={a.max_tier}  categories={a.categories or '-'}")
    print("  Connect with header:  Authorization: Bearer " + raw[:12] + "…")
    return 0


def _cmd_conn_list(_a: argparse.Namespace) -> int:
    keys = connectors.list_keys()
    if not keys:
        print("(no connector keys)")
        return 0
    for k in keys:
        print(f"#{k['id']:<3} {k['name']:<22} [{k['status']}] scope={k['scopes']} "
              f"max_tier={k['max_tier']} cats={k['categories'] or '-'} "
              f"prefix={k['key_prefix'] or '-'} last_used={k['last_used_at'] or 'never'}")
    return 0


def _cmd_conn_revoke(a: argparse.Namespace) -> int:
    print("revoked." if connectors.revoke(a.id) else f"not found: #{a.id}")
    return 0


def _cmd_conn_rotate(a: argparse.Namespace) -> int:
    raw = connectors.rotate(a.id)
    if raw is None:
        print(f"not found: #{a.id}", file=sys.stderr)
        return 2
    print(f"Connector key #{a.id} rotated - new key (copy now):\n\n  {raw}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cortex-gateway-token")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint", help="create an app/generic token")
    m.add_argument("--name", required=True)
    m.add_argument("--scopes", default="app")
    m.add_argument("--max-tier", default="restricted", dest="max_tier",
                   choices=["public", "internal", "confidential", "restricted"])
    m.add_argument("--categories", default=None)
    m.set_defaults(func=_cmd_mint)

    ak = sub.add_parser("admin-key", help="create an admin key for /admin/connectors")
    ak.add_argument("--name", required=True)
    ak.set_defaults(func=_cmd_admin_key)

    sub.add_parser("list", help="list all tokens").set_defaults(func=_cmd_list)

    r = sub.add_parser("revoke", help="revoke any token by id")
    r.add_argument("--id", type=int, required=True)
    r.set_defaults(func=_cmd_revoke)

    # connector subcommands
    conn = sub.add_parser("connector", help="manage connector keys (external AIs)")
    csub = conn.add_subparsers(dest="conn_cmd", required=True)

    cc = csub.add_parser("create")
    cc.add_argument("--name", required=True)
    cc.add_argument("--scope", default="connector:read",
                    help="connector:read | connector:write")
    cc.add_argument("--max-tier", default="internal", dest="max_tier",
                    choices=["public", "internal", "confidential", "restricted"])
    cc.add_argument("--categories", default=None)
    cc.add_argument("--note", default=None)
    cc.add_argument("--expires-at", default=None, dest="expires_at",
                    help="ISO datetime; omit for a long-lived key")
    cc.set_defaults(func=_cmd_conn_create)

    csub.add_parser("list").set_defaults(func=_cmd_conn_list)

    cr = csub.add_parser("revoke")
    cr.add_argument("--id", type=int, required=True)
    cr.set_defaults(func=_cmd_conn_revoke)

    crot = csub.add_parser("rotate")
    crot.add_argument("--id", type=int, required=True)
    crot.set_defaults(func=_cmd_conn_rotate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
