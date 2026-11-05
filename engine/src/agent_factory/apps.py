"""`agent-factory apps`: the factory's products, where each one lives, and its first admin's sign-in.

A web product's chart creates its first admin into the Secret `<release>-admin` (ADR-0044 §3), in
namespace `app-<slug>`; production's release is the product's slug. This reads the factory's own
records for the address and the cluster for the account, so a person can sign in to an app without
digging through kubectl. The password is shown only when asked for (`--password`), never listed.
`admin_from_secret` and `table` are pure; `kubectl_secret` runs kubectl, as the engine does.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_factory.providers.local import namespace

if TYPE_CHECKING:
    from agent_factory.models import Product


@dataclass(frozen=True)
class Admin:
    email: str
    password: str


@dataclass(frozen=True)
class AppRow:
    slug: str
    title: str
    status: str
    address: str
    admin: str  # the admin's email, or why there is none


def secret_name(product: Product) -> str:
    """The chart's `app.adminSecret`: the release name (production's is the slug) cut to 50, plus -admin."""
    return product.slug[:50].rstrip("-") + "-admin"


def admin_from_secret(secret_json: str) -> Admin | None:
    """The admin account in a `kubectl get secret -o json` answer; None when it holds none. Pure."""
    try:
        data = json.loads(secret_json).get("data") or {}
        email = base64.b64decode(data["email"]).decode()
        password = base64.b64decode(data["password"]).decode()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return Admin(email, password) if email and password else None


def kubectl_secret(product: Product) -> str | None:
    """The product's admin Secret as JSON, or None (no such Secret, no cluster, no kubectl)."""
    try:
        res = subprocess.run(  # noqa: S603 - fixed argv, names from the factory's own records
            ["kubectl", "get", "secret", secret_name(product), "-n", namespace(product), "-o", "json"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def row(product: Product, admin: Admin | None) -> AppRow:
    status = "archived" if product.archived_at else str(product.latest_status or "")
    if product.archived_at:
        who = "— (archived: app removed)"
    elif admin:
        who = admin.email
    else:
        who = "— (no admin account in the cluster)"
    return AppRow(product.slug, product.title, status, product.app_url or "— (not delivered yet)", who)


def table(rows: list[AppRow]) -> str:
    """Rows as an aligned plain-text table. Pure."""
    if not rows:
        return "no products yet"
    head = AppRow("APP", "TITLE", "STATUS", "ADDRESS", "ADMIN")
    cols = list(zip(*[(r.slug, r.title, r.status, r.address, r.admin) for r in [head, *rows]], strict=True))
    widths = [max(len(c) for c in col) for col in cols[:-1]]
    lines = []
    for r in [head, *rows]:
        cells = [r.slug, r.title, r.status, r.address]
        lines.append("  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)) + "  " + r.admin)
    return "\n".join(lines)


def detail(product: Product, admin: Admin | None, *, show_password: bool) -> str:
    """One app: where it lives and who signs in. The password only when asked for. Pure."""
    lines = [f"{product.title} ({product.slug})", f"  address: {product.app_url or 'not delivered yet'}"]
    if admin is None:
        lines.append("  admin:   none in the cluster (not a web app with sign-in, or not deployed)")
    else:
        lines.append(f"  email:   {admin.email}")
        lines.append(
            f"  password: {admin.password}" if show_password else "  password: hidden (add --password to show it)"
        )
    return "\n".join(lines)
