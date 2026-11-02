"""Deploy strategy per product (ADR-0046 slice 4), the pure part.

`in-place` (the default) updates production's one Deployment, rolling. `blue-green` runs two
colours of the app in production's Helm release, on its one database: the Service sends users to
the live colour; promotion puts the tested image on the idle colour, checks it at its own preview
address, then switches the Service to it. The colour that served before keeps running until the
next promotion, so switching back is instant: a rollback to the last healthy release's Helm
revision restores the Service to that colour without restarting its pods.

The strategy is chosen per product, defaulted by its stack pack. Blue/green needs a deploy target
that can give the idle colour an address, a chart that renders colours (packs from nextjs-web 1.5.0
and fastapi-service 1.1.0), and a change validated in a temporary environment (ADR-0046 slice 2);
without them a product's change is promoted in place, and the event log says why.

These functions decide and parse; the provider runs Helm (`providers.helm`) and the `promote`
station acts (`engine/stations.py`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_factory.models import Release

IN_PLACE, BLUE_GREEN = "in-place", "blue-green"
STRATEGIES = (IN_PLACE, BLUE_GREEN)
BLUE, GREEN = "blue", "green"
COLORS = (BLUE, GREEN)
PREVIEW_SUFFIX = "-next"  # the idle colour's host: <release>-next.<domain>
# what a chart renders on a pod when it knows colours (the packs' charts from slice 4 on)
COLOR_LABEL = "app.kubernetes.io/color"


def other(color: str) -> str:
    return GREEN if color == BLUE else BLUE


def chosen(product_strategy: str | None, pack_default: str | None) -> tuple[str, str]:
    """The product's strategy and who chose it: the product, its stack pack, or the factory
    (in place, for a product without a pack). Pure."""
    if product_strategy in STRATEGIES:
        return str(product_strategy), "product"
    if pack_default in STRATEGIES:
        return str(pack_default), "pack"
    return IN_PLACE, "factory"


def preview_release(release: str) -> str:
    """The name the idle colour is reached by (`<release>-next`). Pure."""
    return f"{release}{PREVIEW_SUFFIX}"


def in_place_reason(
    provider: object, validated: bool, chart_renders_colors: bool, preview_taken_by: str | None = None
) -> str | None:
    """Why a blue/green product's change is promoted in place this time, or None: blue/green. Pure.
    `validated`: the change ran in a temporary environment, so there is a tested image to stage."""
    if not callable(getattr(provider, "deploy_colors", None)):
        name, kind = getattr(provider, "name", "?"), getattr(provider, "kind", "?")
        return f"deploy target {name} ({kind}) can't give the idle colour its own address"
    if not validated:
        return "the change was validated in place, so there is no tested image to stage beside production"
    if not chart_renders_colors:
        return "the product's chart predates blue/green: it renders no colours"
    if preview_taken_by:
        return f"the idle colour's address is product {preview_taken_by}'s"
    return None


@dataclass(frozen=True)
class Layout:
    """What production's Helm release runs, from the values the factory set."""

    strategy: str
    live: str  # the colour users reach (in place: blue)
    images: dict[str, str] = field(default_factory=dict)  # colour -> repository:tag

    @property
    def live_image(self) -> str | None:
        return self.images.get(self.live)


def _image(node: Any) -> str | None:
    if not isinstance(node, dict) or not node.get("repository") or not node.get("tag"):
        return None
    return f"{node['repository']}:{node['tag']}"


def _json(text: str) -> Any:
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def layout(values_json: str) -> Layout | None:
    """Production's layout from `helm get values -o json`: in place (`image`) or blue/green
    (`strategy`, `blueGreen.live` and each colour's image). None if the output isn't that. Pure."""
    doc = _json(values_json)
    if not isinstance(doc, dict):
        return None
    if doc.get("strategy") == BLUE_GREEN:
        bg = doc.get("blueGreen")
        live = bg.get("live") if isinstance(bg, dict) else None
        if live not in COLORS:
            return None
        assert isinstance(bg, dict)  # noqa: S101 - live came from it
        images = {c: img for c in COLORS if (img := _image(bg.get(c)))}
        return Layout(BLUE_GREEN, str(live), images)
    image = _image(doc.get("image"))
    return Layout(IN_PLACE, BLUE, {BLUE: image}) if image else None


def values_image(values_json: str) -> str | None:
    """`repository:tag` users reach, from `helm get values -o json`: in place the one image, blue/green
    the live colour's. Pure."""
    found = layout(values_json)
    return found.live_image if found else None


@dataclass(frozen=True)
class Plan:
    """A blue/green promotion: stage `images` with `live` still serving, test `idle`, then switch.
    `first`: nothing serves yet, so the tested image goes straight in as the live colour."""

    live: str
    idle: str
    images: dict[str, str]
    first: bool = False


def plan(current: Layout | None, tested: str, healthy: Release | None) -> tuple[Plan | None, list[str]]:
    """How to take `tested` to production blue/green, failing closed. `current`: production's
    layout, None when nothing is installed. Production must serve the last healthy release the
    factory recorded (when there is one): staging keeps the live colour's image as it is, so a
    mismatch would put something unknown back in front of users. Pure."""
    if current is None:
        if healthy is not None:
            return None, [f"production has no release, but iteration {healthy.iteration} is recorded as serving"]
        return Plan(BLUE, BLUE, {BLUE: tested}, first=True), []
    live_image = current.live_image
    if not live_image:
        return None, [f"production's live colour {current.live} has no image"]
    if healthy is not None and healthy.image != live_image:
        return None, [f"production serves {live_image}, not the recorded healthy {healthy.image}"]
    idle = other(current.live)
    return Plan(current.live, idle, {current.live: live_image, idle: tested}), []


def sets(live: str, images: dict[str, str], preview_host: str) -> list[str]:
    """Helm `--set` values for a blue/green release: both colours' images (a colour left out is
    not deployed), the live colour and the idle colour's host. Pure."""
    out = ["strategy=blue-green", f"blueGreen.live={live}", f"blueGreen.previewHost={preview_host}"]
    for color in COLORS:
        repository, _, tag = images.get(color, "").rpartition(":")
        out += [f"blueGreen.{color}.repository={repository}", f"blueGreen.{color}.tag={tag}"]
    return out


def renders_colors(rendered: str) -> bool:
    """A `helm template` of the chart with blue/green values renders both colours. Pure."""
    return f"{COLOR_LABEL}: {GREEN}" in rendered and f"{COLOR_LABEL}: {BLUE}" in rendered
