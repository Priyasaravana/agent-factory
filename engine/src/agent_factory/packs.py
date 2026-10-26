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
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from agent_factory import base_images
from agent_factory.base_images import BaseImage
from agent_factory.decisions import Catalogue, CatalogueError, load_catalogue
from agent_factory.readiness import RuleError, StackRules, parse_stack_rules
from agent_factory.ui_review import Rubric, RubricError, parse_rubric

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


class PackPreview(BaseModel):
    """How the app runs inside the sandbox, without the cluster (ADR-0043 §5): the factory starts
    `command` in the background, waits for `url` + the liveness path, and runs the pack's e2e
    command against `url` before any image is built."""

    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1)  # stays in the foreground; the engine backgrounds it
    url: str = Field(pattern=r"^http://(127\.0\.0\.1|localhost):[0-9]{2,5}$")  # inside the sandbox only
    ready_seconds: int = Field(default=300, ge=10, le=900)  # build + start, before the liveness path answers


class PackAdminSecret(BaseModel):
    """Where a deployment keeps its first admin account (ADR-0044 §3): a Kubernetes Secret the
    pack's chart creates, with the email and password under these keys. The engine reads it after
    deploy (privileged, never recorded) so the live e2e suite and the hidden scenarios can sign in;
    the values are redacted everywhere and reach sandboxes only through the environment."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9{}.-]{1,80}$")  # {release}: the product's Helm release (its slug)
    email: str = Field(default="email", pattern=r"^[A-Za-z0-9._-]{1,60}$")
    password: str = Field(default="password", pattern=r"^[A-Za-z0-9._-]{1,60}$")
    # how a client signs in, for the verifier's prompt (the stack's own route and form)
    sign_in: str = Field(default="Sign in before calling protected routes.", max_length=600)

    def secret_name(self, release: str) -> str:
        return self.name.replace("{release}", release)


class PackDetect(BaseModel):
    """How a team repo is recognised as this stack (ADR-0041 §5)."""

    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(default_factory=list)  # paths relative to the folder, e.g. package.json
    deps: list[str] = Field(default_factory=list)  # declared in one of those files, e.g. next

    @field_validator("files")
    @classmethod
    def _inside_repo(cls, v: list[str]) -> list[str]:
        for f in v:
            parts = PurePosixPath(f).parts
            if not f or f.startswith(("/", "~")) or "\\" in f or ".." in parts or any(c in f for c in "*?["):
                raise ValueError(f"detect.files: '{f}' must be a plain file path inside the repo")
        return v


class PackUi(BaseModel):
    """Look and feel (ADR-0044 §4): the designer's skill, the UI review rubric, and where the e2e
    suite saves its route screenshots (relative to the product repo)."""

    model_config = ConfigDict(extra="forbid")
    skill: str  # a folder with SKILL.md, preloaded for the designer
    rubric: str  # criteria the reviewer scores the screenshots against (1–5, reported only)
    screenshots: str = Field(default="e2e-screenshots", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")


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
    preview: PackPreview | None = None  # e2e before deploy (ADR-0043 §5); needs `e2e`
    port: int = Field(ge=1, le=65535)
    health: PackHealth = PackHealth()
    chart: str = "deploy/chart"
    egress: list[str] = Field(default_factory=list)  # for this pack's products' sandboxes only
    detect: PackDetect = PackDetect()
    readiness: str = "readiness.yaml"
    evals: str | None = None
    decisions: str | None = None  # the design questions every product of this stack answers (ADR-0044 §1)
    admin_secret: PackAdminSecret | None = None  # the deployment's first admin, for live sign-in (§3)
    # approved runtime images, default first (ADR-0045): stages of the template's Dockerfile
    base_images: list[BaseImage] = Field(default_factory=list, max_length=6)
    ui: PackUi | None = None  # designer, screenshots and UI review (ADR-0044 §4)


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
    _catalogue: Catalogue | None = PrivateAttr(default=None)
    _rubric: Rubric | None = PrivateAttr(default=None)

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
    def catalogue(self) -> Catalogue | None:
        """The decision sheet's questions (ADR-0044 §1); None when the pack asks none."""
        return self._catalogue

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
    if stack.base_images:
        dockerfile = pack.path(stack.template) / "Dockerfile"
        text = dockerfile.read_text() if dockerfile.is_file() else ""
        if not base_images.has_runtime_arg(text):
            raise PackError(
                f"pack {pack.source}: stack.base_images needs the template's Dockerfile to pick its runtime "
                "with `ARG RUNTIME` and `FROM ${RUNTIME}`"
            )
        have = base_images.stages_in(text)
        if missing := [b.stage for b in stack.base_images if b.stage not in have]:
            raise PackError(
                f"pack {pack.source}: stack.base_images stages not in the template's Dockerfile: {', '.join(missing)}"
            )
        if len({b.stage for b in stack.base_images}) != len(stack.base_images):
            raise PackError(f"pack {pack.source}: stack.base_images lists a stage twice")
    if stack.preview and not stack.e2e:
        raise PackError(f"pack {pack.source}: stack.preview needs stack.e2e, the suite it runs against the preview")
    if stack.ui:
        ui_skill = _inside(pack, stack.ui.skill, "stack.ui.skill", is_dir=True)
        if not (ui_skill / "SKILL.md").is_file():
            raise PackError(f"pack {pack.source}: stack.ui.skill '{stack.ui.skill}' has no SKILL.md")
        rubric_file = _inside(pack, stack.ui.rubric, "stack.ui.rubric", is_dir=False)
        try:
            raw = yaml.safe_load(rubric_file.read_text())
            pack._rubric = parse_rubric(raw, f"pack {pack.source} {stack.ui.rubric}")
        except (RubricError, yaml.YAMLError) as exc:
            raise PackError(str(exc)) from exc
        if not stack.e2e:
            raise PackError(f"pack {pack.source}: stack.ui needs stack.e2e, the suite that takes the screenshots")
    if stack.evals:
        _check_evals(pack, _inside(pack, stack.evals, "stack.evals", is_dir=False))
    if stack.decisions:
        cat_file = _inside(pack, stack.decisions, "stack.decisions", is_dir=False)
        try:
            pack._catalogue = load_catalogue(
                yaml.safe_load(cat_file.read_text()), f"pack {pack.source} {stack.decisions}"
            )
        except (CatalogueError, yaml.YAMLError) as exc:
            raise PackError(str(exc)) from exc
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
    detect: PackDetect = field(default_factory=PackDetect)  # how a team repo is recognised (§5)
    preview: PackPreview | None = None  # the app inside the sandbox, for e2e before deploy (ADR-0043 §5)
    decisions: Catalogue | None = None  # the decision sheet's questions (ADR-0044 §1)
    admin_secret: PackAdminSecret | None = None  # the deployment's first admin (ADR-0044 §3)
    base_images: tuple[BaseImage, ...] = ()  # approved runtime images, default first (ADR-0045)
    # look and feel (ADR-0044 §4): the designer's skill, the UI rubric, where screenshots are saved
    ui_skill: str | None = None
    ui_skill_file: Path | None = None
    ui_rubric: Rubric | None = None
    ui_screenshots: str | None = None

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
        bp.bind_pack(binding_of(pack, overrides))


def binding_of(pack: Pack, overrides: dict[str, dict[str, Any]] | None = None) -> PackBinding:
    """What the engine keeps of an installed stack pack at run time."""
    stack = pack.stack
    assert stack is not None  # noqa: S101 - stack packs only
    skill = pack.path(stack.skill) / "SKILL.md" if stack.skill else None
    return PackBinding(
        pack.id,
        pack.version,
        pack.sha256,
        pack.title,
        stack.builds,
        pack.skill_name,
        skill,
        pack.rules,
        overrides or {},
        stack.summary,
        stack.health.live,
        stack.health.ready,
        stack.e2e,
        tuple(stack.egress),
        tuple(stack.toolchain),
        stack.detect,
        stack.preview,
        pack.catalogue,
        stack.admin_secret,
        tuple(stack.base_images),
        ui_skill=Path(stack.ui.skill).name if stack.ui else None,
        ui_skill_file=pack.path(stack.ui.skill) / "SKILL.md" if stack.ui else None,
        ui_rubric=pack._rubric,
        ui_screenshots=stack.ui.screenshots if stack.ui else None,
    )


def install(cfg: FactoryConfig, home: Path) -> list[Pack]:
    """Load the packs listed under `packs:` (relative to the factory home) and bind the blueprints
    to them. Refuses an unknown or duplicate pack; a pack that fails its checks stops the start."""
    packs = [load_pack(home / rel) for rel in cfg.packs]
    ids = [p.id for p in packs]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise PackError(f"packs listed twice: {', '.join(dupes)}")
    bind(cfg, packs, home)
    cfg.bind_stack_packs({p.id: binding_of(p) for p in packs if p.stack is not None})
    return packs


# ------------------------------------------------- team repos (ADR-0041 §5) --
# folders one level down that are never a stack's home
_SKIP_FOLDERS = IGNORED | {"docs", "doc", "vendor", "dist", "build", "out", "target", "tmp"}
MAX_FOLDERS = 60  # a repo with more top-level folders than this is scanned at the root and these


@dataclass(frozen=True)
class Match:
    """A stack pack recognised in a team repo: guidance for the folder it was found in."""

    id: str
    version: str
    title: str
    folder: str  # "" = the repository root
    matched: tuple[str, ...]  # the files and dependencies that matched
    egress: tuple[str, ...] = ()  # what the repo's sandboxes may reach for this pack (§7)

    def as_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "folder": self.folder,
            "matched": list(self.matched),
            "egress": list(self.egress),
        }


def _candidate_folders(root: Path) -> list[str]:
    """The root, then each folder one level down (sorted), without caches, docs or build output."""
    try:
        subs = sorted(
            d.name
            for d in root.iterdir()
            if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and d.name not in _SKIP_FOLDERS
        )
    except OSError:
        subs = []
    return ["", *subs[:MAX_FOLDERS]]


def _declared(path: Path) -> str | set[str]:
    """What a manifest declares: package names from a package.json, else its text (pyproject.toml,
    requirements.txt and other manifests are searched for the name as a word)."""
    text = path.read_text(errors="replace") if path.is_file() and not path.is_symlink() else ""
    if path.name != "package.json":
        return text.lower()
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return set()
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            names |= {str(k).lower() for k in data[key]}
    return names


def _declares(found: str | set[str], dep: str) -> bool:
    dep = dep.lower()
    if isinstance(found, set):
        return dep in found
    return re.search(rf"(?<![\w.-]){re.escape(dep)}(?![\w-])", found) is not None


def detect(packs: list[PackBinding], root: Path) -> list[Match]:
    """Which installed stack packs a team repo is built with (ADR-0041 §5), at the root and one
    folder down. A pack matches a folder when every `detect.files` entry is there and every
    `detect.deps` entry is declared in one of those files. A pack found at the root is not
    looked for again below it. Pure: reads files, runs nothing."""
    out: list[Match] = []
    folders = _candidate_folders(root)
    for pack in sorted(packs, key=lambda b: b.id):
        rule = pack.detect
        if not rule.files:
            continue  # a pack that says nothing about how to recognise it is never guessed
        for folder in folders:
            base = root / folder if folder else root
            manifests = [base / f for f in rule.files]
            if not all(m.is_file() and not m.is_symlink() for m in manifests):
                continue
            declared = [_declared(m) for m in manifests]
            if not all(any(_declares(d, dep) for d in declared) for dep in rule.deps):
                continue
            where = f"{folder}/" if folder else ""
            matched = tuple([*(where + f for f in rule.files), *rule.deps])
            out.append(Match(pack.id, pack.version, pack.title or pack.id, folder, matched, pack.egress))
            if not folder:
                break
    return sorted(out, key=lambda m: (m.folder, m.id))


def advisory(pack: PackBinding, root: Path, folder: str) -> dict[str, Any]:
    """How one folder of a team repo measures against a detected pack's golden path: the pack's
    stack-specific signals and its own (ADR-0041 §5). Advisory: never part of the repo's Level,
    which stays stack-neutral (`assess.score`). Pure: reads files, runs nothing."""
    from agent_factory.readiness import STACK_SIGNALS, score

    rules = pack.rules
    signals: list[dict[str, Any]] = []
    if rules is not None:
        base = root / folder if folder else root
        own = set(STACK_SIGNALS) | {s.id for s in rules.extra}
        signals = [
            {
                "id": s.id,
                "title": s.title,
                "level": s.level,
                "pillar": s.pillar,
                "ok": ok,
                "hint": "" if ok else s.hint,
            }
            for s, ok in score(base, None, rules).results
            if s.id in own
        ]
    return {
        "pack": pack.id,
        "version": pack.version,
        "title": pack.title or pack.id,
        "folder": folder,
        "met": sum(1 for s in signals if s["ok"]),
        "total": len(signals),
        "signals": signals,
    }


PREVIEW_NOT_READY = "PREVIEW NOT READY"


def preview_e2e_script(preview: PackPreview, e2e: str, live_path: str) -> str:
    """One shell script for the sandbox (ADR-0043 §5): start the pack's preview in the background,
    wait until its liveness path answers, run the e2e suite against it, stop the preview. Pure.
    Exit codes: the e2e suite's, or 3 when the preview never answered (it prints PREVIEW_NOT_READY
    and the preview's output). When e2e fails, the preview's output follows the test output: a
    server error is often the reason."""
    probe = preview.url.rstrip("/") + live_path
    return "\n".join(
        [
            "set -u",
            # the preview is on this sandbox's own loopback: never through the egress proxy, which
            # refuses it (the sandbox sets NO_PROXY empty so everything else still goes through it)
            "export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost",
            'log="$(mktemp)"',
            f'setsid bash -c {shlex.quote(preview.command)} >"$log" 2>&1 &',
            "pid=$!",
            "stop() {",
            '  kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true',
            "}",
            "trap stop EXIT",
            f"deadline=$((SECONDS + {preview.ready_seconds}))",
            "up=0",
            'while [ "$SECONDS" -lt "$deadline" ]; do',
            f"  if curl -fs -o /dev/null --max-time 5 {shlex.quote(probe)}; then up=1; break; fi",
            '  kill -0 "$pid" 2>/dev/null || break',
            "  sleep 2",
            "done",
            'if [ "$up" != 1 ]; then',
            f"  echo {shlex.quote(f'{PREVIEW_NOT_READY}: {probe} did not answer')}",
            '  echo "--- preview output (last 80 lines) ---"',
            '  tail -n 80 "$log"',
            "  exit 3",
            "fi",
            f"( {e2e.replace('{url}', preview.url)} )",
            "rc=$?",
            'if [ "$rc" != 0 ]; then echo "--- preview output (last 60 lines) ---"; tail -n 60 "$log"; fi',
            'exit "$rc"',
        ]
    )
