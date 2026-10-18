"""What the sandbox image provides to the apps the factory builds (ADR-0041 §4). Pure.

A stack pack lists what its `make verify` / `make e2e` need under `stack.toolchain`
(`python@3.12`, `uv`, `node@22`, `npm`, `chromium`). The image always has the interpreters it
declares in `images/sandbox/Dockerfile` (`APP_PYTHONS`, the Node base image, uv, npm). Optional
**layers** are built only when an installed pack asks for one, so an install that builds only
FastAPI services keeps the small image:

    chromium    a headless Chromium for Playwright (end-to-end and accessibility tests),
                installed for the Playwright version the Dockerfile pins (`PLAYWRIGHT_VERSION`);
                a pack's template pins the same version, or Playwright looks for another build

Anything else a pack asks for is not provided, and preflight refuses its products until the
sandbox image offers it (`missing`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

LAYERS: dict[str, str] = {"chromium": "headless Chromium for Playwright"}


@dataclass(frozen=True)
class SandboxTools:
    """What the sandbox Dockerfile declares (`ARG`s and the Node base image)."""

    pythons: tuple[str, ...]
    node: str | None
    playwright: str | None

    def always(self) -> set[str]:
        out = {"uv", "npm", *(f"python@{v}" for v in self.pythons)}
        if self.node:
            out.add(f"node@{self.node}")
        return out


def read_dockerfile(text: str) -> SandboxTools:
    pythons = re.search(r'^ARG APP_PYTHONS="([^"]+)"', text, re.M)
    node = re.search(r"^FROM node:(\d+)", text, re.M)
    playwright = re.search(r'^ARG PLAYWRIGHT_VERSION="?([0-9][^"\s]*)"?', text, re.M)
    return SandboxTools(
        tuple(pythons.group(1).split()) if pythons else (),
        node.group(1) if node else None,
        playwright.group(1) if playwright else None,
    )


def from_home(home: Path) -> SandboxTools:
    file = home / "images" / "sandbox" / "Dockerfile"
    return read_dockerfile(file.read_text()) if file.is_file() else SandboxTools((), None, None)


def layers_for(toolchains: Iterable[Iterable[str]]) -> list[str]:
    """The optional layers the installed packs ask for, in a stable order."""
    wanted = {item for chain in toolchains for item in chain}
    return [layer for layer in sorted(LAYERS) if layer in wanted]


def missing(toolchain: Iterable[str], tools: SandboxTools, layers: Iterable[str]) -> list[str]:
    """What a pack asks for that this sandbox image doesn't provide."""
    have = tools.always() | set(layers)
    return [item for item in toolchain if item not in have]


def build_args(layers: Iterable[str], tools: SandboxTools) -> list[str]:
    """`docker build` arguments for the requested layers."""
    chosen = list(layers)
    if not chosen:
        return []
    args = [f"--build-arg BROWSERS={','.join(chosen)}"]
    if tools.playwright:
        args.append(f"--build-arg PLAYWRIGHT_VERSION={tools.playwright}")
    return args
