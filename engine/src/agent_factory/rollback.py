"""Rollback to the last healthy release (ADR-0046 slice 1), the pure part.

A change that fails at deploy, or fails live acceptance after its deploy, would otherwise leave its
own (unaccepted) release serving while the repair loop runs, or after the change stops. The engine
restores the product's last healthy release first, after the station has collected its diagnostics,
so repair keeps its evidence.

It fails closed: a release is restored only when its record is complete (target, cluster,
namespace, Helm release, revision, image and digest), names the deploy target in use now, and its
revision is in the release history with the recorded image. Nothing ever rolls back to "the previous
revision". After the rollback, the restored release must be the one Helm reports, roll out, run the
recorded digest and answer its liveness and readiness paths.

These functions decide and parse; the provider runs the commands (`providers.helm_release`).
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_factory.models import Change, Product, Release

# the station handlers whose failure means production may be serving this change's release:
# deploy (the upgrade may have half-applied) and acceptance (it tests the deployed release)
ROLLBACK_HANDLERS = frozenset({"deploy", "acceptance"})
# what identifies where a release runs; recorded at deploy, checked before a rollback
IDENTITY = ("environment", "target", "cluster", "namespace", "helm_release")
# history states a revision can be restored from (never a failed or pending one)
RESTORABLE = frozenset({"deployed", "superseded"})
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def can_restore(provider: object) -> bool:
    """The deploy target can record and restore releases (it has `identity` and `rollback`). Pure."""
    return callable(getattr(provider, "identity", None)) and callable(getattr(provider, "rollback", None))


def target(handler: str, change: Change, product: Product, environment: str) -> Release | None:
    """The release to restore after `handler` failed, or None: nothing to do. Pure.

    Only a product with a healthy release on record in the same environment can be rolled back
    (the first delivery has none). Deploy always restores, since a failed upgrade may have
    replaced pods; acceptance only when this change's release is what is serving."""
    last = product.release
    if handler not in ROLLBACK_HANDLERS or last is None or last.environment != environment:
        return None
    if last.change_id == change.id:
        return None  # this change is the healthy release: nothing older to restore
    if handler == "acceptance" and not serving_unaccepted(change, product):
        return None
    return last


def serving_unaccepted(change: Change, product: Product) -> bool:
    """This change's deploy started (its intent is recorded before the cluster is touched) and its
    release isn't the product's healthy one: production may be serving something no one accepted.
    Pure."""
    d = change.deployed
    return (
        d is not None
        and d.change_id == change.id
        and (product.release is None or product.release.change_id != change.id)
    )


def record_problems(release: Release) -> list[str]:
    """What a release record lacks to be restorable later. Pure. An incomplete record is never
    accepted as healthy, so nothing has to guess at rollback time."""
    missing = [f for f in ("target", "cluster", "namespace", "helm_release", "image") if not getattr(release, f)]
    out = [f"no {', '.join(missing)} recorded"] if missing else []
    if not (isinstance(release.revision, int) and release.revision > 0):
        out.append("no Helm revision recorded")
    if not (release.digest and _DIGEST.fullmatch(release.digest)):
        out.append("no image digest recorded")
    return out


def identity_problems(release: Release, now: dict[str, str]) -> list[str]:
    """Where the recorded release runs against where the deploy target points now (`now` has the
    IDENTITY keys). A mismatch means restoring it would act on another cluster, namespace or release.
    Pure."""
    return [
        f"recorded {key} '{getattr(release, key)}' is not the current '{now.get(key, '')}'"
        for key in IDENTITY
        if str(getattr(release, key)) != str(now.get(key, ""))
    ]


def before_rollback(release: Release, now: dict[str, str]) -> list[str]:
    """Every reason not to roll back to `release` at all. Pure."""
    return record_problems(release) + identity_problems(release, now)


# ---------------------------------------------------------------- parsers --
def _json(text: str) -> Any:
    text = (text or "").strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    try:
        return json.loads(text[min(starts) :])
    except json.JSONDecodeError:
        return None


def _rev(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def helm_revision(status_json: str) -> int | None:
    """The release's revision from `helm status -o json` output (its `version`); None if the output
    isn't that. Pure."""
    doc = _json(status_json)
    return _rev(doc.get("version")) if isinstance(doc, dict) else None


def history(history_json: str) -> list[dict[str, Any]]:
    """`helm history -o json` as a list of {revision, status, description}; [] if it isn't that.
    Pure."""
    doc = _json(history_json)
    if not isinstance(doc, list):
        return []
    out = []
    for row in doc:
        if isinstance(row, dict) and (rev := _rev(row.get("revision"))):
            out.append(
                {"revision": rev, "status": str(row.get("status", "")), "description": str(row.get("description", ""))}
            )
    return out


def values_image(values_json: str) -> str | None:
    """`repository:tag` from `helm get values -o json` (the image the factory set). Pure."""
    doc = _json(values_json)
    image = doc.get("image") if isinstance(doc, dict) else None
    if not isinstance(image, dict) or not image.get("repository") or not image.get("tag"):
        return None
    return f"{image['repository']}:{image['tag']}"


def normalize_digest(image_id: str) -> str | None:
    """The `sha256:<64 hex>` in a container status imageID (`docker.io/library/app@sha256:…`,
    `sha256:…`), or None. Pure."""
    found = _DIGEST.findall(image_id or "")
    return found[-1] if found else None


def live_digests(pods_output: str) -> list[str | None]:
    """One digest per container of every pod not being deleted, from lines of
    `<deletionTimestamp>|<imageID> <imageID> …` (the provider's jsonpath). None for a container
    whose imageID has no digest. Pure."""
    out: list[str | None] = []
    for line in (pods_output or "").splitlines():
        if "|" not in line:
            continue
        deleting, ids = line.split("|", 1)
        if deleting.strip():
            continue  # a pod on its way out: the release it belonged to is no longer serving
        out += [normalize_digest(i) for i in ids.split()]
    return out


def one_digest(pods_output: str) -> tuple[str | None, str]:
    """The single digest every live container runs, or (None, why not). Pure."""
    digests = live_digests(pods_output)
    if not digests:
        return None, "no running pods of the release"
    if None in digests:
        return None, "a running container reports no image digest"
    if len(set(digests)) > 1:
        return None, f"running pods disagree on the image: {', '.join(sorted({d for d in digests if d}))}"
    return digests[0], ""


# ------------------------------------------------------------ the checks --
def history_problems(release: Release, rows: list[dict[str, Any]], image_at_revision: str | None) -> list[str]:
    """The recorded revision must be in the release history, restorable, and carry the recorded
    image. Pure."""
    row = next((r for r in rows if r["revision"] == release.revision), None)
    if row is None:
        return [f"revision {release.revision} is not in the release history of {release.helm_release}"]
    out = []
    if row["status"] not in RESTORABLE:
        out.append(f"revision {release.revision} is '{row['status']}', not a release that ran")
    if image_at_revision != release.image:
        out.append(f"revision {release.revision} runs {image_at_revision or 'an unknown image'}, not {release.image}")
    return out


def restored_problems(
    release: Release, latest: dict[str, Any] | None, image_now: str | None, pods_output: str
) -> list[str]:
    """After `helm rollback`: Helm's newest revision is the rollback to the recorded one and
    deployed, its values carry the recorded image, and every live container runs the recorded
    digest. Pure. (Rollout and the health paths are checked by the provider.)"""
    out = []
    want = f"Rollback to {release.revision}"
    if latest is None:
        out.append("no release history after the rollback")
    else:
        if latest["status"] != "deployed":
            out.append(f"the newest revision {latest['revision']} is '{latest['status']}', not deployed")
        if latest["description"] != want:
            out.append(f"the newest revision {latest['revision']} is '{latest['description']}', not '{want}'")
    if image_now != release.image:
        out.append(f"the release now runs {image_now or 'an unknown image'}, not {release.image}")
    digest, why = one_digest(pods_output)
    if digest is None:
        out.append(why)
    elif digest != release.digest:
        out.append(f"running pods use {digest}, not the recorded {release.digest}")
    return out


def note(release: Release, ok: bool, summary: str) -> str:
    """What the repair station and the evidence are told. Stable for the same release and outcome,
    so the no-progress stop still recognises a repeated failure. Pure."""
    rev = f", revision {release.revision}" if release.revision else ""
    if ok:
        return (
            f"\n--- production ---\nProduction was rolled back to iteration {release.iteration} "
            f"({release.image}{rev}) and passed its checks: users are not affected while this is fixed."
        )
    return (
        f"\n--- production ---\nProduction was NOT rolled back to iteration {release.iteration} "
        f"({release.image}{rev}): {summary}"
    )
