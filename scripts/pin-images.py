#!/usr/bin/env python3
"""Every image the factory is built from or runs is pinned by digest, and every tool it downloads by
checksum (S-07). Python 3 standard library only.

    scripts/pin-images.py --check      offline: every FROM, COPY --from and compose image carries
                                       @sha256:…, and each downloaded tool has a pinned checksum
                                       for every architecture (CI `images`, tests/test_locked_images.py)
    scripts/pin-images.py --resolve    online (needs `docker buildx`): what each tag points at now, and
                                       the checksums the tools' release hosts publish for the pinned
                                       versions. A pinned checksum that differs from the published one
                                       fails (a wrong pin, or a changed download); a digest that is no
                                       longer its tag's newest is only reported (Dependabot moves it).
    scripts/pin-images.py --resolve --write
                                       pin what --resolve found into the files (to add a new image or
                                       move a tool's version: change the tag or version, then run this)

A reference keeps its tag next to the digest (`python:3.12-slim-bookworm@sha256:…`), so a reader and
Dependabot still see which version it is; the digest decides what is pulled.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILES = [
    "images/factory/Dockerfile",
    "images/auth/Dockerfile",
    "images/sandbox/Dockerfile",
    "web/Dockerfile",
]
COMPOSE = "docker-compose.yml"
ARCHES = ("amd64", "arm64")
# Downloaded tools in the factory image: version ARG -> the checksum file its release host publishes
TOOLS = {
    "KUBECTL": "https://dl.k8s.io/release/{v}/bin/linux/{arch}/kubectl.sha256",
    "HELM": "https://get.helm.sh/helm-{v}-linux-{arch}.tar.gz.sha256sum",
}
TOOL_FILE = "images/factory/Dockerfile"

_FROM = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(?P<ref>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.I | re.M)
_COPY_FROM = re.compile(r"^COPY\s+(?:--\S+\s+)*?--from=(?P<ref>\S+)", re.I | re.M)
_IMAGE = re.compile(r"^\s*image:\s*(?P<raw>\S+)", re.M)
_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(?P<ref>[^}]+)\}$")


@dataclass(frozen=True)
class Ref:
    file: str
    ref: str  # as written: tag, tag@digest or digest

    @property
    def pinned(self) -> bool:
        return bool(re.search(r"@sha256:[0-9a-f]{64}$", self.ref))

    @property
    def tag(self) -> str:
        return self.ref.split("@", 1)[0]


def refs(root: Path = ROOT) -> list[Ref]:
    """Every image reference in the Dockerfiles and the compose file (not earlier build stages)."""
    out: list[Ref] = []
    for name in DOCKERFILES:
        text = (root / name).read_text()
        stages: set[str] = set()
        for m in _FROM.finditer(text):
            ref = m.group("ref")
            if ref.lower() != "scratch" and ref not in stages:
                out.append(Ref(name, ref))
            if m.group("stage"):
                stages.add(m.group("stage"))
        for m in _COPY_FROM.finditer(text):
            ref = m.group("ref")
            if ref not in stages and not ref.isdigit():
                out.append(Ref(name, ref))
    for m in _IMAGE.finditer((root / COMPOSE).read_text()):
        raw = m.group("raw").strip("\"'")
        d = _DEFAULT.match(raw)
        out.append(Ref(COMPOSE, d.group("ref") if d else raw))
    return out


def tool_pins(root: Path = ROOT) -> dict[str, dict[str, str | None]]:
    """For each tool: its version and the pinned checksum per architecture (None when not pinned)."""
    text = (root / TOOL_FILE).read_text()
    out: dict[str, dict[str, str | None]] = {}
    for tool in TOOLS:
        v = re.search(rf"^ARG {tool}_VERSION=(\S+)", text, re.M)
        pins: dict[str, str | None] = {"version": v.group(1) if v else None}
        for arch in ARCHES:
            s = re.search(rf"^ARG {tool}_SHA256_{arch.upper()}=([0-9a-f]{{64}})$", text, re.M)
            pins[arch] = s.group(1) if s else None
        out[tool] = pins
    return out


def check(root: Path = ROOT) -> list[str]:
    """Pure (reads files only): every problem, empty when everything is pinned."""
    problems = [f"{r.file}: {r.ref} is not pinned by digest (@sha256:…)" for r in refs(root) if not r.pinned]
    for tool, pins in tool_pins(root).items():
        if not pins["version"]:
            problems.append(f"{TOOL_FILE}: no ARG {tool}_VERSION")
        problems += [f"{TOOL_FILE}: no ARG {tool}_SHA256_{a.upper()}" for a in ARCHES if not pins[a]]
    return problems


# ------------------------------------------------------------------ online --
def digest_of(tag: str) -> str:
    res = subprocess.run(  # noqa: S603
        ["docker", "buildx", "imagetools", "inspect", tag, "--format", "{{json .Manifest}}"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"{tag}: {res.stderr.strip() or 'inspect failed'}")
    return str(json.loads(res.stdout)["digest"])


def published_sum(tool: str, version: str, arch: str) -> str:
    url = TOOLS[tool].format(v=version, arch=arch)
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - fixed https hosts
        text = r.read().decode().strip()
    s = text.split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", s):
        raise RuntimeError(f"{url}: not a sha256: {text[:80]!r}")
    return s


def resolve(write: bool, root: Path = ROOT) -> int:
    failed = 0
    found: dict[str, str] = {}
    for r in refs(root):
        if r.tag in found:
            continue
        try:
            found[r.tag] = digest_of(r.tag)
        except RuntimeError as exc:
            print(f"FAIL  {exc}")
            failed = 1
            continue
    for r in refs(root):
        d = found.get(r.tag)
        if d is None:
            continue
        want = f"{r.tag}@{d}"
        state = "ok   " if r.ref == want else ("stale" if r.pinned else "UNPINNED")
        print(f"{state} {r.file}: {want}")
    sums: dict[str, str] = {}
    for tool, pins in tool_pins(root).items():
        for arch in ARCHES:
            try:
                s = published_sum(tool, str(pins["version"]), arch)
            except (OSError, RuntimeError) as exc:
                print(f"FAIL  {tool} {pins['version']} {arch}: {exc}")
                failed = 1
                continue
            sums[f"{tool}_SHA256_{arch.upper()}"] = s
            pinned = pins[arch]
            if pinned and pinned != s:
                print(f"FAIL  {tool} {pins['version']} {arch}: pinned {pinned}, published {s}")
                failed = 1
            else:
                print(f"{'ok   ' if pinned else 'UNPINNED'} ARG {tool}_SHA256_{arch.upper()}={s}")
    if write:
        _write(root, found, sums)
    return failed


def _write(root: Path, digests: dict[str, str], sums: dict[str, str]) -> None:
    for name in [*DOCKERFILES, COMPOSE]:
        p = root / name
        text = p.read_text()
        for tag, d in digests.items():
            text = re.sub(rf"(?<![\w./@]){re.escape(tag)}(?:@sha256:[0-9a-f]{{64}})?(?![\w.:/@-])", f"{tag}@{d}", text)
        if name == TOOL_FILE:
            for arg, s in sums.items():
                line = f"ARG {arg}={s}"
                if re.search(rf"^ARG {arg}=", text, re.M):
                    text = re.sub(rf"^ARG {arg}=\S*$", line, text, flags=re.M)
                else:  # after the tool's last ARG (its version, or a sum already written)
                    tool = arg.split("_SHA256_")[0]
                    last = list(re.finditer(rf"^ARG {tool}_(?:VERSION|SHA256_[A-Z0-9]+)=\S*$", text, re.M))[-1]
                    text = text[: last.end()] + "\n" + line + text[last.end() :]
        p.write_text(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--resolve", action="store_true")
    ap.add_argument("--write", action="store_true", help="with --resolve: pin what was found")
    a = ap.parse_args(argv)
    if a.check:
        problems = check()
        for p in problems:
            print(f"FAIL  {p}")
        print("every image pinned by digest, every download by checksum" if not problems else "NOT PINNED")
        return 1 if problems else 0
    return resolve(a.write)


if __name__ == "__main__":
    sys.exit(main())
