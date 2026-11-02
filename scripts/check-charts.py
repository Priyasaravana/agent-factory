#!/usr/bin/env python3
"""The stack packs' charts in every deploy strategy (ADR-0046 slice 4), rendered by real Helm.

For each pack chart: `helm lint` in place and blue/green, then `helm template` in place, a first
blue/green release, blue/green staged (blue serving, green new) and switched; each render is parsed
and checked for what the engine relies on: blue keeps the Deployment the app always had, the two
colours never select each other's pods, the Service sends users to the live colour only, the preview
reaches the idle colour only, and bad values fail the render. CI runs it (job `charts`).

usage: scripts/check-charts.py [chart ...]   (HELM=<command> to use another helm binary)
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHARTS = [ROOT / "packs/fastapi-service/template/deploy/chart", ROOT / "packs/nextjs-web/template/deploy/chart"]
HELM = shlex.split(os.environ.get("HELM", "helm"))
R = "t"  # release name
INGRESS = ["service.type=ClusterIP", "ingress.enabled=true", f"ingress.host={R}.apps.example.com", "ingress.tls=true"]


def bg(live: str, blue: str | None, green: str | None) -> list[str]:
    sets = ["strategy=blue-green", f"blueGreen.live={live}", f"blueGreen.previewHost={R}-next.apps.example.com"]
    for color, image in (("blue", blue), ("green", green)):
        repo, _, tag = (image or ":").rpartition(":")
        sets += [f"blueGreen.{color}.repository={repo}", f"blueGreen.{color}.tag={tag}"]
    return sets


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*HELM, *args], capture_output=True, text=True, check=False)


def render(chart: Path, sets: list[str]) -> dict[tuple[str, str], dict]:
    out = run(["template", R, str(chart), *[a for s in sets for a in ("--set", s)]])
    if out.returncode:
        raise AssertionError(f"helm template failed: {out.stderr.strip()[-600:]}")
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out.stdout) if d}


def deployments(objs: dict) -> dict[str, dict]:
    return {name: d for (kind, name), d in objs.items() if kind == "Deployment"}


def pod_labels(d: dict) -> dict:
    return d["spec"]["template"]["metadata"]["labels"]


def selects(selector: dict, labels: dict) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


def check(chart: Path) -> list[str]:
    problems: list[str] = []

    def expect(ok: bool, what: str) -> None:
        if not ok:
            name = chart.parent.parent.parent.name if chart.name == "chart" and chart.parent.name == "deploy" else chart
            problems.append(f"{name}: {what}")

    for mode, sets in (("in place", INGRESS), ("blue/green", INGRESS + bg("blue", "app:1", "app:2"))):
        lint = run(["lint", str(chart), *[a for s in sets for a in ("--set", s)]])
        expect(lint.returncode == 0, f"helm lint ({mode}) failed: {lint.stdout.strip()[-400:]}")

    in_place = render(chart, ["image.repository=app", "image.tag=1", *INGRESS])
    deps = deployments(in_place)
    expect(list(deps) == [R], f"in place renders one Deployment named {R}, not {list(deps)}")
    blue = deps.get(R)
    if blue:
        expect(pod_labels(blue).get("app.kubernetes.io/color") == "blue", "in place, the Deployment is the blue colour")
        expect("app:1" in str(blue["spec"]["template"]["spec"]["containers"][0]["image"]), "in place runs `image`")
        expect(selects(in_place[("Service", R)]["spec"]["selector"], pod_labels(blue)), "the Service selects the app")
    expect(("Service", f"{R}-preview") not in in_place, "no preview in place")

    first = render(chart, [*INGRESS, *bg("blue", "app:1", None)])
    expect(list(deployments(first)) == [R], "a first blue/green release runs blue only")
    expect(("Ingress", f"{R}-preview") not in first, "no preview without an idle colour")

    for live, idle in (("blue", "green"), ("green", "blue")):
        objs = render(chart, [*INGRESS, *bg(live, "app:1", "app:2")])
        deps = deployments(objs)
        expect(sorted(deps) == sorted([R, f"{R}-green"]), f"blue/green renders {R} and {R}-green, not {sorted(deps)}")
        if len(deps) != 2:
            continue
        by_color = {pod_labels(d)["app.kubernetes.io/color"]: d for d in deps.values()}
        expect(set(by_color) == {"blue", "green"}, "one Deployment per colour")
        if set(by_color) != {"blue", "green"}:
            continue
        expect(by_color["blue"]["metadata"]["name"] == R, "blue keeps the name the app always had")
        if blue:
            expect(
                by_color["blue"]["spec"]["selector"] == blue["spec"]["selector"],
                "blue keeps the selector it had in place (a Deployment's selector can't change)",
            )
        for color, d in by_color.items():
            other = "green" if color == "blue" else "blue"
            mine = d["spec"]["selector"]["matchLabels"]
            expect(selects(mine, pod_labels(d)), f"{color}'s selector matches its own pods")
            expect(not selects(mine, pod_labels(by_color[other])), f"{color}'s selector never matches {other}'s pods")
        image = {c: d["spec"]["template"]["spec"]["containers"][0]["image"] for c, d in by_color.items()}
        expect(image == {"blue": "app:1", "green": "app:2"}, f"each colour runs its own image, not {image}")
        svc = objs[("Service", R)]["spec"]["selector"]
        expect(selects(svc, pod_labels(by_color[live])), f"users ({live} live) reach {live}")
        expect(not selects(svc, pod_labels(by_color[idle])), f"users ({live} live) never reach {idle}")
        preview = objs.get(("Service", f"{R}-preview"))
        expect(preview is not None, "an idle colour has a preview Service")
        if preview:
            expect(
                selects(preview["spec"]["selector"], pod_labels(by_color[idle])), "the preview reaches the idle colour"
            )
            expect(
                not selects(preview["spec"]["selector"], pod_labels(by_color[live])),
                "the preview never reaches users' colour",
            )
        ing = objs.get(("Ingress", f"{R}-preview"))
        expect(ing is not None and ing["spec"]["rules"][0]["host"] == f"{R}-next.apps.example.com", "preview host")
        expect(objs[("Ingress", R)]["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == R,
               "the main Ingress stays on the main Service")  # fmt: skip

    for bad, why in (
        (["strategy=canary"], "an unknown strategy"),
        ([*bg("green", "app:1", None)], "a live colour without an image"),
        (
            ["strategy=blue-green", "blueGreen.live=purple", "blueGreen.blue.repository=app", "blueGreen.blue.tag=1"],
            "an unknown colour",
        ),
    ):
        out = run(["template", R, str(chart), *[a for s in bad for a in ("--set", s)]])
        expect(out.returncode != 0, f"{why} must fail the render")
    return problems


def main() -> int:
    charts = [Path(a) for a in sys.argv[1:]] or CHARTS
    problems = [p for chart in charts for p in check(chart)]
    for p in problems:
        print(f"FAIL {p}")
    print(f"{'FAILED' if problems else 'ok'}: {len(charts)} chart(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
