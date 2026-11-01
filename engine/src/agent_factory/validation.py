"""Validate in a temporary environment, then promote (ADR-0046 slices 2 and 3), the pure part.

A change to a product is deployed first as its own Helm release in its own namespace with its own
address (`<slug>-c<change>` at `<slug>-c<change>.<domain>`), where live e2e, the hidden-scenario
verifier and the repair loop run. Production is not touched until acceptance passes; then the
`promote` station deploys the same image there, checks it runs the digest that was tested, runs the
smoke check and the pack's e2e, and only then records the release as healthy. The temporary
release is removed when the change stops, whatever the outcome, on archive, and by a sweep at
start-up.

A deploy target that can't give a second address (the `local` NodePort provider), or a lane with
no promote station (an older pinned version), validates in place and relies on rollback (slice 1);
the event log says which and why.

These functions decide; the pipeline and the stations act (`engine/pipeline.py`, `engine/stations.py`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_factory.models import Change, Validation

# the stations that act on the temporary environment when a change has one
VALIDATE_HANDLERS = frozenset({"deploy", "deploy-repair", "acceptance"})
# the station that takes the tested image to production
PROMOTE = "promote"
HELM_RELEASE_MAX = 53  # Helm's limit on a release name (it becomes a label value and a DNS name)


def release_name(slug: str, change_id: str) -> str:
    """The temporary release of `change_id`: `<slug>-c<change id>`, the slug shortened (never the
    change id) so the whole fits Helm's 53 characters and stays a DNS label. Pure."""
    suffix = f"-c{change_id.lower()}"
    head = slug[: max(1, HELM_RELEASE_MAX - len(suffix))].rstrip("-") or "app"
    return f"{head}{suffix}"


def in_place_reason(provider: object, stations: Iterable[str]) -> str | None:
    """Why this change validates in place, or None: it gets a temporary environment. Pure.
    `stations` are the handlers of the change's lane."""
    handlers = set(stations)
    if "deploy" not in handlers:
        return "the lane has no deploy station"
    if not getattr(provider, "validates_separately", False) or not callable(getattr(provider, "identity", None)):
        name, kind = getattr(provider, "name", "?"), getattr(provider, "kind", "?")
        return f"deploy target {name} ({kind}) can't give a change a second address"
    if PROMOTE not in handlers:
        return "the lane has no promote station (an older lane version): production would never be updated"
    return None


def live(change: Change) -> Validation | None:
    """The change's temporary environment while it exists. Pure."""
    v = change.validation
    return v if v is not None and v.removed_at is None else None


def slot(handler: str, change: Change) -> str | None:
    """The Helm release a station acts on instead of production's, or None: production. Only the
    validating stations, and only while the change's temporary environment exists. Pure."""
    v = live(change)
    return v.helm_release if v is not None and handler in VALIDATE_HANDLERS else None


def needs_redeploy(handler: str, change: Change) -> bool:
    """A station that tests the temporary environment would run after it was removed (a resume after
    a stop): it must be deployed again first, never tested against production instead. Pure."""
    v = change.validation
    return handler == "acceptance" and v is not None and v.removed_at is not None


def promote_problems(v: Validation | None) -> list[str]:
    """What the temporary environment's record lacks for promotion of exactly what was tested. Pure."""
    if v is None:
        return ["nothing was validated in a temporary environment"]
    out = []
    if not v.image or ":" not in v.image:
        out.append("no tested image recorded")
    if not (v.digest or "").startswith("sha256:"):
        out.append("no digest of the tested image recorded")
    return out


def same_image(v: Validation, digest: str | None) -> list[str]:
    """Production must run the digest the tests ran (the same artifact, never rebuilt). Pure."""
    if digest != v.digest:
        return [f"production runs {digest or 'an unknown digest'}, not the tested {v.digest}"]
    return []


# what identifies where a temporary environment runs; recorded before deploy, compared before removal
IDENTITY = ("environment", "target", "cluster", "namespace", "helm_release")


def removal_problems(v: Validation, product_slug: str, now: Mapping[str, str] | None) -> list[str]:
    """Every reason not to remove the recorded temporary environment with the deploy target as it is
    configured now, fail closed. `now` is the target's identity for that release name (environment,
    target, cluster, namespace, Helm release), or None: the target can't tell. The configuration may
    have changed since the change ran (another cluster, kube context or namespace prefix): deleting
    then would remove a same-named release somewhere else and leave the real one behind. Pure."""
    if not v.helm_release or v.helm_release == product_slug:
        return ["its name is the product's own release"]
    if now is None:
        return [f"deploy target {v.target} can't say where its releases run"]
    missing = [k for k in IDENTITY if not str(getattr(v, k) or "")]
    if missing:
        return [f"no {', '.join(missing)} recorded"]
    return [
        f"recorded {key} '{getattr(v, key)}' is not the current '{now.get(key, '')}'"
        for key in IDENTITY
        if str(getattr(v, key)) != str(now.get(key, ""))
    ]
