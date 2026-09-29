"""`factory-auth`: run the service, or recover access from inside the container.

factory-auth serve
factory-auth reset-password <user>      # prints a one-time password; must be changed at sign-in
factory-auth users
"""

from __future__ import annotations

import argparse
import secrets

from factory_auth.app import Settings
from factory_auth.store import Store


def main() -> None:
    p = argparse.ArgumentParser(prog="factory-auth")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    serve.add_argument("--port", type=int, default=8001)
    rp = sub.add_parser("reset-password", help="set a one-time password (break-glass)")
    rp.add_argument("username")
    sub.add_parser("users")
    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("factory_auth.app:app_factory", factory=True, host=args.host, port=args.port)
        return
    store = Store(Settings().data_dir / "auth.db")
    if args.cmd == "users":
        for u in store.list_users():
            flags = " ".join(f for f, on in (("disabled", u.disabled), ("must-change", u.must_change)) if on)
            print(f"{u.username:<20} {u.role:<7} {flags}")
    elif args.cmd == "reset-password":
        if not store.get_user(args.username):
            raise SystemExit(f"no user '{args.username}'")
        password = secrets.token_urlsafe(18)
        store.set_password(args.username, password, must_change=True)
        store.update_user(args.username, disabled=False)
        store.audit("cli", "break-glass password reset", args.username)
        print(f"one-time password for {args.username}: {password}\n(it must be changed at the next sign-in)")
