"""Rollback to the last healthy release (ADR-0046 slice 1), the pure part.

A change that fails at deploy, or fails live acceptance after its deploy, would otherwise leave its
own (broken) release serving while the repair loop runs. The engine restores the product's last
healthy release first, after the station has collected its diagnostics, so repair keeps its
evidence. These functions decide whether to and what to say; the provider does the rollback.
"""

from __future__ import annotations

import json

from agent_factory.models import Change, Product, Release

# the station handlers whose failure means production may be serving this change's release:
# deploy (the upgrade may have half-applied) and acceptance (it tests the deployed release)
ROLLBACK_HANDLERS = frozenset({"deploy", "acceptance"})


def target(handler: str, change: Change, product: Product, environment: str) -> Release | None:
    """The release to restore after `handler` failed, or None: nothing to do. Pure.

    Only a product with a healthy release on record in the same environment can be rolled back
    (the first delivery has none). Deploy always restores, since a failed upgrade may have
    replaced pods; acceptance only when this change's deploy is what is serving."""
    last = product.release
    if handler not in ROLLBACK_HANDLERS or last is None or last.environment != environment:
        return None
    if last.change_id == change.id:
        return None  # this change is the healthy release (already delivered): nothing older to restore
    if handler == "acceptance" and (change.deployed is None or change.deployed.change_id != change.id):
        return None
    return last


def helm_revision(status_json: str) -> int | None:
    """The release's revision from `helm status -o json` output (its `version`); None if the output
    isn't that. Pure."""
    text = status_json.strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        doc = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    version = doc.get("version") if isinstance(doc, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else None


def note(release: Release, ok: bool, summary: str) -> str:
    """What the repair station and the evidence are told. Stable for the same release and outcome,
    so the no-progress stop still recognises a repeated failure. Pure."""
    rev = f", revision {release.revision}" if release.revision else ""
    if ok:
        return (
            f"\n--- production ---\nProduction was rolled back to iteration {release.iteration} "
            f"({release.image}{rev}) and answers its health check: users are not affected while this is fixed."
        )
    return (
        f"\n--- production ---\nRolling production back to iteration {release.iteration} "
        f"({release.image}{rev}) failed: {summary}"
    )
