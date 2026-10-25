"""Approved base images and factory-wide image risk decisions (ADR-0045, pure).

A stack pack lists the runtime images it approves, in order (`stack.base_images`); the template's
Dockerfile has one stage per image and a global `ARG RUNTIME` that picks one. When the default image
holds only on base-image findings that have a fix released (its publisher hasn't shipped it yet), the
Build station tries the next image: `plan_fallback` says which, and `fallback_worth_trying` whether
a verdict is the kind a fallback can cure.

A person's acceptance of a base-image risk is a factory decision: it is kept per image (repository
and tag, without the digest) and finding key, honoured for every product on that image
(`accepted_for`), and closed when a scan of the image no longer reports it (`close_fixed`).
Nothing here does IO; the station and the actions read and write the store.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_factory.image_scan import ImageFinding, Verdict

META_KEY = "base_image_risks"  # where the decisions live in the state store's meta table


def image_name(ref: str) -> str:
    """An image reference without its digest: what a factory decision is about, so it carries over
    when the publisher rebuilds the same tag. `repo:tag@sha256:…` → `repo:tag`."""
    return ref.split("@", 1)[0].strip().lower()


def registry_of(ref: str) -> str:
    """The registry host of an image reference (`docker.io` when it names none)."""
    first = image_name(ref).split("/", 1)[0]
    return first if "/" in image_name(ref) and ("." in first or ":" in first or first == "localhost") else "docker.io"


# ------------------------------------------------------------- the pack --
class BaseImage(BaseModel):
    """One approved runtime image of a stack pack (ADR-0045 §1)."""

    image: str = Field(min_length=3, max_length=200)
    stage: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")  # the Dockerfile stage that holds it
    login: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,30}$")  # key in registry_logins
    title: str = ""


def stages_in(dockerfile: str) -> set[str]:
    """Stage names a Dockerfile declares (`FROM … AS name`)."""
    return {m.group(1).lower() for m in re.finditer(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", dockerfile, re.M | re.I)}


def has_runtime_arg(dockerfile: str) -> bool:
    """True when the Dockerfile picks its runtime stage with `ARG RUNTIME` (template ≥ 1.3.1)."""
    return re.search(r"^\s*ARG\s+RUNTIME\b", dockerfile, re.M | re.I) is not None and bool(
        re.search(r"^\s*FROM\s+\$\{?RUNTIME\}?", dockerfile, re.M | re.I)
    )


def candidates(approved: Sequence[BaseImage], dockerfile: str) -> list[BaseImage]:
    """The approved images this product's Dockerfile can build, in the pack's order. Empty when the
    Dockerfile has no `ARG RUNTIME` (built before 1.3.1, or changed by hand): nothing to fall back to."""
    if not has_runtime_arg(dockerfile):
        return []
    have = stages_in(dockerfile)
    return [b for b in approved if b.stage in have]


def fallback_worth_trying(verdict: Verdict) -> bool:
    """A hold that another base image may cure: it holds on the base image's own findings (fixed
    upstream but not shipped, or with no fix in this distribution; another image may not carry the
    package at all). Not a repeated app fix, and not an app dependency with no fix released: those
    are the app's, whatever it runs on."""
    if verdict.action != "hold" or verdict.repeated:
        return False
    if any(f.origin == "app" for f in verdict.no_fix):
        return False
    return any(f.origin == "base" for f in [*verdict.base_fixes, *verdict.no_fix])


def plan_fallback(cands: Sequence[BaseImage], tried: Iterable[str], logins: set[str]) -> list[BaseImage]:
    """The images still to try, in order: not yet tried, and signed in when their registry needs it."""
    done = set(tried)
    return [b for b in cands if b.stage not in done and (b.login is None or b.login in logins)]


# ---------------------------------------------------- factory decisions --
class ImageRisk(BaseModel):
    """A person accepted the risk of one finding in one base image, for every product (ADR-0045 §3)."""

    image: str  # image_name(): repository and tag, no digest
    key: str  # ImageFinding.key, e.g. "image cve-2026-31789 libssl3"
    cve: str = ""
    until: datetime | None = None
    reason: str = ""
    by: str | None = None
    at: datetime
    product_id: str = ""  # where it was decided
    item_id: str = ""


def load(raw: Any) -> list[ImageRisk]:
    """Decisions from their stored JSON (anything unreadable is dropped, never fatal)."""
    out = []
    for r in raw if isinstance(raw, list) else []:
        try:
            out.append(ImageRisk.model_validate(r))
        except ValueError:
            continue
    return out


def dump(risks: Sequence[ImageRisk]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in risks]


def active(risk: ImageRisk, now: datetime) -> bool:
    return risk.until is None or risk.until > now


def accepted_for(risks: Sequence[ImageRisk], image: str, keys: Iterable[str], now: datetime) -> set[str]:
    """Finding keys of this base image that a factory decision covers (unexpired)."""
    name = image_name(image)
    ok = {r.key for r in risks if r.image == name and active(r, now)}
    return {k for k in keys if k in ok}


def record(
    risks: Sequence[ImageRisk],
    image: str,
    findings: Iterable[tuple[str, str]],
    *,
    until: datetime | None,
    reason: str,
    by: str | None,
    now: datetime,
    product_id: str = "",
    item_id: str = "",
) -> list[ImageRisk]:
    """Add (or renew) the decisions for these (key, cve) findings of one image."""
    name = image_name(image)
    new = {k: c for k, c in findings}
    kept = [r for r in risks if not (r.image == name and r.key in new)]
    added = [
        ImageRisk(
            image=name, key=k, cve=c, until=until, reason=reason, by=by, at=now, product_id=product_id, item_id=item_id
        )
        for k, c in new.items()
    ]
    return [*kept, *added]


def close_fixed(
    risks: Sequence[ImageRisk], image: str, present: Iterable[str], now: datetime
) -> tuple[list[ImageRisk], list[ImageRisk]]:
    """After a scan of `image`: drop its decisions whose finding the scan no longer reports, and any
    that have expired (any image). Returns (kept, closed)."""
    name, seen = image_name(image), set(present)
    kept: list[ImageRisk] = []
    closed: list[ImageRisk] = []
    for r in risks:
        gone = r.image == name and r.key not in seen
        (closed if gone or not active(r, now) else kept).append(r)
    return kept, closed


def base_findings(findings: Iterable[ImageFinding]) -> list[ImageFinding]:
    """The findings a factory decision may cover: the base image's, not the app's dependencies."""
    return [f for f in findings if f.origin == "base"]


# ---------------------------------------------------------- the store --
def load_risks(store: Any) -> list[ImageRisk]:
    """The factory's base-image decisions from the state store (meta key `base_image_risks`)."""
    import json

    raw = store.get_meta(META_KEY)
    try:
        return load(json.loads(raw)) if raw else []
    except ValueError:
        return []


def save_risks(store: Any, risks: Sequence[ImageRisk]) -> None:
    import json

    store.set_meta(META_KEY, json.dumps(dump(risks)))


def published_port(docker_port_output: str) -> int | None:
    """The host port from `docker port <name> <port>` (`0.0.0.0:49153`, maybe an IPv6 line too)."""
    for line in docker_port_output.splitlines():
        m = re.search(r":(\d{2,5})\s*$", line.strip())
        if m:
            return int(m.group(1))
    return None
