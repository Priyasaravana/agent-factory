"""Packs (ADR-0039 §2, ADR-0041): admin-installed, reviewed folders with a `pack.yaml`.

A pack may add phases, station templates (ADR-0039, see `phases.py`), a stack, or any mix. A
**stack pack** has a `stack:` section and says *what to build*: the template a new product's repo
starts from, the golden-path skill its agents follow, the verify command, the port and chart, and
how the stack proves the stack-specific readiness signals (`readiness.yaml`, ADR-0041 §3):

    id: fastapi-service
    version: 1.0.0
    title: FastAPI service
    stack:
      builds: service
      summary: Python FastAPI REST service ...
      technologies: [python, fastapi, postgres, ...]
      template: template
      skill: skills/fastapi-golden-path
      verify: make verify
      port: 8000
      chart: deploy/chart
      readiness: readiness.yaml

A **blueprint** in the config names its pack (`pack: fastapi-service`) and keeps what is specific
to this install: environment, node ports, scan commands, minimum level. `install` loads the packs
listed under `packs:` and binds each blueprint to its pack; a field the config sets anyway
overrides the pack's, and the override is recorded in each change's evidence.

Packs are a supply-chain input (ADR-0041 §7): each change records the pack's id, version and the
SHA-256 of its folder (`folder_sha256`) in its provenance and sealed evidence.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from agent_factory.readiness import RuleError, StackRules, parse_stack_rules

if TYPE_CHECKING:
    from agent_factory.config import FactoryConfig


class PackError(ValueError):
    pass


class PackPhase(BaseModel):
    id: str
    title: str = Field(min_length=2, max_length=60)
    allows: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=lambda: ["report"])


_BOTH: tuple[Literal["factory", "repo"], ...] = ("factory", "repo")


class PackStation(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")
    title: str = Field(min_length=2, max_length=60)
    hint: str = Field(default="", max_length=200)
    phase: str
    works_on: list[Literal["factory", "repo"]] = Field(default_factory=lambda: list(_BOTH), min_length=1)
    needs: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=list)
    uses: list[str] = Field(default_factory=lambda: ["read-repo"])
    agent: str  # path of its agent spec inside the pack, e.g. agents/reviewer.md


Builds = Literal["service", "web-app", "spa", "full-stack", "static-site", "agent-service", "worker"]


class PackHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    live: str = "/healthz"
    ready: str = "/readyz"


class PackDetect(BaseModel):
    """How a team repo is recognised as this stack (ADR-0041 §5)."""

    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)


class PackStack(BaseModel):
    """What a stack pack builds (ADR-0041 §1). Paths are inside the pack folder."""

    model_config = ConfigDict(extra="forbid")
    builds: Builds
    summary: str = Field(min_length=10, max_length=200)
    technologies: list[str] = Field(min_length=1)
    template: str
    skill: str | None = None
    toolchain: list[str] = Field(default_factory=list)  # what the sandbox image must provide
    verify: str = "make verify"
    e2e: str | None = None  # {url}: the deployed app
    port: int = Field(ge=1, le=65535)
    health: PackHealth = PackHealth()
    chart: str = "deploy/chart"
    egress: list[str] = Field(default_factory=list)  # for this pack's products' sandboxes only
    detect: PackDetect = PackDetect()
    readiness: str = "readiness.yaml"
    evals: str | None = None


class Pack(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    version: str = Field(pattern=r"^[0-9][0-9A-Za-z.+-]{0,30}$")
    title: str = ""
    phases: list[PackPhase] = Field(default_factory=list)
    stations: list[PackStation] = Field(default_factory=list)
    stack: PackStack | None = None
    folder: Path | None = None
    _sha256: str = PrivateAttr(default="")
    _rules: StackRules | None = PrivateAttr(default=None)

    @property
    def source(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def sha256(self) -> str:
        """SHA-256 of the pack folder as loaded (`folder_sha256`)."""
        return self._sha256

    @property
    def rules(self) -> StackRules | None:
        """How this stack proves the stack-specific readiness signals (stack packs only)."""
        return self._rules

    @property
    def skill_name(self) -> str | None:
        return Path(self.stack.skill).name if self.stack and self.stack.skill else None

    def path(self, rel: str) -> Path:
        assert self.folder is not None  # noqa: S101 - set by load_pack
        return self.folder / rel


# never part of what a pack is: tool caches and local environments a developer may leave behind
IGNORED = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
        ".DS_Store",
        # Node builds and test runs (nextjs-web and other web packs)
        ".next",
        "coverage",
        "test-results",
        "playwright-report",
        "next-env.d.ts",
        "tsconfig.tsbuildinfo",
    }
)


def folder_sha256(folder: Path) -> str:
    """One digest for every file in the folder: path and content, in path order. Stable across
    machines (POSIX paths, no timestamps); caches in `IGNORED` don't count."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED)
        for name in sorted(filenames):
            if name in IGNORED or name.endswith(".pyc"):
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(folder).as_posix()
            body = os.readlink(p).encode() if p.is_symlink() else p.read_bytes()
            h.update(rel.encode() + b"\0" + hashlib.sha256(body).hexdigest().encode() + b"\n")
    return h.hexdigest()


def _inside(pack: Pack, rel: str, what: str, *, is_dir: bool) -> Path:
    assert pack.folder is not None  # noqa: S101 - set by load_pack
    root = pack.folder.resolve()
    p = (pack.folder / rel).resolve()
    ok = (p == root or root in p.parents) and (p.is_dir() if is_dir else p.is_file())
    if not ok:
        raise PackError(f"pack {pack.source}: {what} '{rel}' is not a {'folder' if is_dir else 'file'} in the pack")
    return p


def _check_stack(pack: Pack) -> StackRules:
    stack = pack.stack
    assert stack is not None  # noqa: S101
    _inside(pack, stack.template, "stack.template", is_dir=True)
    if stack.skill:
        skill = _inside(pack, stack.skill, "stack.skill", is_dir=True)
        if not (skill / "SKILL.md").is_file():
            raise PackError(f"pack {pack.source}: stack.skill '{stack.skill}' has no SKILL.md")
    if stack.evals:
        _check_evals(pack, _inside(pack, stack.evals, "stack.evals", is_dir=False))
    file = _inside(pack, stack.readiness, "stack.readiness", is_dir=False)
    try:
        return parse_stack_rules(yaml.safe_load(file.read_text()), f"pack {pack.source} {stack.readiness}")
    except (RuleError, yaml.YAMLError) as exc:
        raise PackError(str(exc)) from exc


def _check_evals(pack: Pack, file: Path) -> None:
    """A pack's evaluation cases (ADR-0041 §6): a list of cases with distinct ids."""
    from agent_factory.workflow import EvalCase

    where = f"pack {pack.source}: {file.name}"
    try:
        raw = yaml.safe_load(file.read_text())
        cases = [EvalCase.model_validate(c) for c in raw] if isinstance(raw, list) else None
    except (yaml.YAMLError, ValueError) as exc:
        raise PackError(f"{where}: {exc}") from exc
    if not cases:
        raise PackError(f"{where}: a non-empty list of cases (id, title, requirements)")
    ids = [c.id for c in cases]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise PackError(f"{where}: case ids listed twice: {', '.join(dupes)}")


def load_pack(folder: Path) -> Pack:
    file = folder / "pack.yaml"
    if not file.is_file():
        raise PackError(f"pack {folder}: no pack.yaml")
    raw = yaml.safe_load(file.read_text()) or {}
    try:
        pack = Pack.model_validate({**raw, "folder": folder})
    except ValueError as exc:
        raise PackError(f"pack {folder}: {exc}") from exc
    if pack.stack is not None:
        pack._rules = _check_stack(pack)
    pack._sha256 = folder_sha256(folder)
    return pack


# ----------------------------------------------------------------- binding --
@dataclass(frozen=True)
class PackBinding:
    """The pack a blueprint builds with, as this install runs it (ADR-0041 §1)."""

    id: str
    version: str
    sha256: str
    title: str
    builds: str
    skill: str | None
    skill_file: Path | None
    rules: StackRules | None
    # fields the config sets anyway: {field: {"pack": value, "config": value}}
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary: str = ""  # one line for the stack card on the create form (ADR-0041 §2)
    health_live: str = "/healthz"  # what the deploy smoke check calls
    health_ready: str = "/readyz"
    e2e: str | None = None  # the pack's end-to-end command; {url}: the deployed app
    egress: tuple[str, ...] = ()  # added to this pack's products' sandboxes only (ADR-0041 §7)
    toolchain: tuple[str, ...] = ()  # what the sandbox image must provide (ADR-0041 §4)

    @property
    def source(self) -> str:
        return f"{self.id}@{self.version}"

    def evidence(self) -> dict[str, Any]:
        """What each change records in its provenance and sealed manifest (ADR-0041 §7)."""
        return {"id": self.id, "version": self.version, "sha256": self.sha256, "overrides": self.overrides}


def _pack_values(pack: Pack, home: Path) -> dict[str, Any]:
    stack = pack.stack
    assert stack is not None and pack.folder is not None  # noqa: S101
    template = pack.folder / stack.template
    try:
        template_ref = os.path.relpath(template, home)
    except ValueError:  # another drive (Windows)
        template_ref = str(template)
    if template_ref.startswith(".."):
        template_ref = str(template.resolve())
    return {
        "template": Path(template_ref).as_posix(),
        "verify_command": stack.verify,
        "stack": list(stack.technologies),
        "service_port": stack.port,
        "chart_path": stack.chart,
    }


def bind(cfg: FactoryConfig, packs: list[Pack], home: Path) -> None:
    """Give each blueprint that names a pack the pack's template, verify command, technologies,
    port and chart. What the config sets explicitly wins and is recorded as an override."""
    by_id = {p.id: p for p in packs}
    for name, bp in cfg.blueprints.items():
        if bp.pack is None:
            continue
        where = f"blueprint '{name}'"
        pack = by_id.get(bp.pack)
        if pack is None:
            listed = ", ".join(sorted(by_id)) or "none"
            raise PackError(f"{where} names pack '{bp.pack}', which is not installed (`packs:` lists {listed})")
        if pack.stack is None:
            raise PackError(f"{where}: pack {pack.source} has no `stack:` section, so it builds nothing")
        if bp.target != "new":
            raise PackError(f"{where}: only blueprints that build new products name a pack")
        explicit = set(bp.model_fields_set)
        overrides: dict[str, dict[str, Any]] = {}
        for key, value in _pack_values(pack, home).items():
            current = getattr(bp, key)
            if key in explicit and current is not None and current != value:
                overrides[key] = {"pack": value, "config": current}
            else:
                setattr(bp, key, value)
        skill = pack.path(pack.stack.skill) / "SKILL.md" if pack.stack.skill else None
        bp.bind_pack(
            PackBinding(
                pack.id,
                pack.version,
                pack.sha256,
                pack.title,
                pack.stack.builds,
                pack.skill_name,
                skill,
                pack.rules,
                overrides,
                pack.stack.summary,
                pack.stack.health.live,
                pack.stack.health.ready,
                pack.stack.e2e,
                tuple(pack.stack.egress),
                tuple(pack.stack.toolchain),
            )
        )


def install(cfg: FactoryConfig, home: Path) -> list[Pack]:
    """Load the packs listed under `packs:` (relative to the factory home) and bind the blueprints
    to them. Refuses an unknown or duplicate pack; a pack that fails its checks stops the start."""
    packs = [load_pack(home / rel) for rel in cfg.packs]
    ids = [p.id for p in packs]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise PackError(f"packs listed twice: {', '.join(dupes)}")
    bind(cfg, packs, home)
    return packs
