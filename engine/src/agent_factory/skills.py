"""Skills library: the factory's own skills shipped in plugin/skills, plus
skills imported from GitHub (including the configured defaults, e.g. BuilderIO/skills).

Imported skills are pinned to a commit. Every installed commit is kept in the DB,
so a workflow version that pinned an older commit still gets exactly that
content. Before a run, the skills pinned by its workflow version are written
into a content-addressed local plugin that the agent runner loads next to the
factory plugin.

A skill is instructions, and optionally scripts that an agent may run with its
own tools. Imports are reviewed first: the preview shows every file, flags
scripts, and diffs against the installed commit. Installing a skill with
scripts needs an explicit `accept_scripts`. The guardrail hooks still apply to
anything an agent runs.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_factory.config import SkillSource
    from agent_factory.github import Fetched
    from agent_factory.state.base import StateStore
    from agent_factory.workflow import WorkflowDoc

IMPORTED_PLUGIN = "imported-skills"
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SCRIPT_EXT = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".cmd"}
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class SkillError(Exception):
    """Invalid, unknown or unsafe skill operation (HTTP 409)."""


class SkillFile(BaseModel):
    path: str
    size: int
    script: bool
    content: str


class SkillRecord(BaseModel):
    """One installed commit of an imported skill."""

    name: str
    description: str = ""
    repo: str
    path: str
    ref: str
    sha: str
    files: dict[str, str]  # relative path -> text
    installed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def scripts(self) -> list[str]:
        return [p for p, c in self.files.items() if is_script(p, c)]


class SkillPreview(BaseModel):
    name: str
    description: str
    repo: str
    path: str
    ref: str
    sha: str
    files: list[SkillFile]
    scripts: list[str]
    problems: list[str]  # installing is blocked while non-empty
    installed_sha: str | None = None  # the commit currently installed under this name
    diff: str | None = None  # unified diff installed -> this commit
    up_to_date: bool = False


def is_script(path: str, content: str) -> bool:
    return Path(path).suffix.lower() in SCRIPT_EXT or content.startswith("#!")


def skill_meta(skill_md: str, fallback_name: str) -> tuple[str, str]:
    m = _FRONT.match(skill_md)
    meta = (yaml.safe_load(m.group(1)) or {}) if m else {}
    if not isinstance(meta, dict):
        meta = {}
    return str(meta.get("name") or fallback_name), str(meta.get("description") or "").strip()


def read_folder(root: Path) -> tuple[dict[str, str], list[str]]:
    """Text files of a skill folder, and any problems (binary files, no SKILL.md)."""
    files: dict[str, str] = {}
    problems: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        try:
            files[rel] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"'{rel}' is not a text file (binary files are not imported)")
    if "SKILL.md" not in files:
        problems.append("the folder has no SKILL.md at its root")
    return files, problems


def seed_dir(seeds_dir: Path, repo: str, sha: str, path: str) -> Path:
    return seeds_dir / repo / sha / path.strip("/")


def _diff(old: dict[str, str], new: dict[str, str]) -> str:
    out: list[str] = []
    for path in sorted(set(old) | set(new)):
        a, b = old.get(path), new.get(path)
        if a == b:
            continue
        out += difflib.unified_diff(
            (a or "").splitlines(keepends=True),
            (b or "").splitlines(keepends=True),
            fromfile=f"a/{path}" if a is not None else "/dev/null",
            tofile=f"b/{path}" if b is not None else "/dev/null",
        )
    return "".join(out)


class SkillLibrary:
    def __init__(self, store: StateStore, builtin_dir: Path, cache_dir: Path) -> None:
        self.store = store
        self.builtin_dir = builtin_dir
        self.cache_dir = cache_dir

    # ---------------------------------------------------------------- read --
    def builtin_names(self) -> set[str]:
        return {p.parent.name for p in self.builtin_dir.glob("*/SKILL.md")}

    def imported(self) -> list[SkillRecord]:
        return self.store.current_skills()

    def imported_names(self) -> set[str]:
        return {r.name for r in self.imported()}

    def get(self, name: str, sha: str | None = None) -> SkillRecord:
        rec = self.store.get_skill_version(name, sha) if sha else self.store.current_skill(name)
        if rec is None:
            raise SkillError(f"imported skill '{name}'{'@' + sha[:12] if sha else ''} not found")
        return rec

    def versions(self, name: str) -> list[SkillRecord]:
        return self.store.skill_versions(name)

    # ------------------------------------------------------------- preview --
    def preview(self, fetched: Fetched) -> SkillPreview:
        files, problems = read_folder(fetched.root)
        fallback = Path(fetched.path).name or fetched.repo.split("/")[-1]
        name, description = skill_meta(files.get("SKILL.md", ""), fallback)
        if not SKILL_NAME.match(name):
            problems.append(f"skill name '{name}' must be lowercase letters, digits and '-' (max 64)")
        if name in self.builtin_names():
            problems.append(f"'{name}' is a built-in skill shipped with the factory; imports cannot replace it")
        if not description:
            problems.append("SKILL.md frontmatter needs a description (agents use it to decide when to load the skill)")
        current = self.store.current_skill(name)
        if current and (current.repo, current.path) != (fetched.repo, fetched.path):
            problems.append(
                f"'{name}' is already installed from {current.repo}/{current.path}; remove it first to switch source"
            )
        return SkillPreview(
            name=name,
            description=description,
            repo=fetched.repo,
            path=fetched.path,
            ref=fetched.ref,
            sha=fetched.sha,
            files=[
                SkillFile(path=p, size=len(c.encode()), script=is_script(p, c), content=c) for p, c in files.items()
            ],
            scripts=[p for p, c in files.items() if is_script(p, c)],
            problems=problems,
            installed_sha=current.sha if current else None,
            diff=(_diff(current.files, files) or None) if current and current.sha != fetched.sha else None,
            # a newer commit that doesn't touch this skill's folder is not an update
            up_to_date=bool(current and (current.sha == fetched.sha or current.files == files)),
        )

    # ------------------------------------------------------------- install --
    def install(self, fetched: Fetched, accept_scripts: bool = False) -> SkillRecord:
        pv = self.preview(fetched)
        if pv.problems:
            raise SkillError("cannot install: " + "; ".join(pv.problems))
        if pv.scripts and not accept_scripts:
            raise SkillError(f"the skill contains scripts {pv.scripts}; review them and confirm with accept_scripts")
        files, _ = read_folder(fetched.root)
        rec = SkillRecord(
            name=pv.name,
            description=pv.description,
            repo=fetched.repo,
            path=fetched.path,
            ref=fetched.ref,
            sha=fetched.sha,
            files=files,
        )
        self.store.save_skill_version(rec)
        self.store.set_current_skill(rec.name, rec.sha)
        return rec

    def remove(self, name: str) -> None:
        """Hide the skill from the picker. Old commits stay stored, so workflow
        versions that pinned them keep working."""
        self.get(name)
        self.store.set_current_skill(name, None)

    # ------------------------------------------------------------ defaults --
    def ensure_defaults(
        self,
        sources: list[SkillSource],
        seeds_dir: Path,
        fetch: Callable[[str, str, str], Fetched],
    ) -> list[str]:
        """First start: install the configured default skills at their pinned
        commit, from the copy baked into the image or else from GitHub. A skill
        that was ever installed is left alone (updated or removed by a human).
        Returns problems; the factory still starts without them."""
        from agent_factory.github import Fetched

        problems: list[str] = []
        for src in sources:
            for path in src.paths:
                if self.store.skill_versions(Path(path).name):
                    continue
                seed = seed_dir(seeds_dir, src.repo, src.sha, path)
                try:
                    if (seed / "SKILL.md").exists():
                        fetched = Fetched(src.repo, path, src.ref, src.sha, seed, _tmp=seeds_dir / ".none")
                    else:
                        fetched = fetch(src.repo, path, src.sha)
                        fetched.ref = src.ref
                    try:
                        name, _ = skill_meta((fetched.root / "SKILL.md").read_text(), Path(path).name)
                        if not self.store.skill_versions(name):
                            self.install(fetched, accept_scripts=True)  # reviewed via the pin in config
                    finally:
                        if fetched.root != seed:
                            fetched.cleanup()
                except Exception as exc:  # noqa: BLE001 - one bad default must not stop the factory
                    problems.append(f"default skill {src.repo}/{path}@{src.sha[:7]}: {exc}")
        return problems

    # ---------------------------------------------------------------- pins --
    def effective_pins(self, doc: WorkflowDoc) -> dict[str, str]:
        """Pins a run uses: the version's own pins, plus the installed commit for
        imported skills an older version used before it recorded pins (e.g. the
        BuilderIO skills that used to ship inside the image)."""
        pins = dict(doc.skill_pins)
        current = {r.name: r.sha for r in self.imported()}
        for a in doc.agents.values():
            for s in a.skills:
                if s not in pins and s in current and s not in self.builtin_names():
                    pins[s] = current[s]
        return pins

    def pins_for(self, doc: WorkflowDoc) -> dict[str, str]:
        """Pins for publishing: the installed commit of every imported skill the
        workflow's agents use (an earlier pin is kept only if the skill was removed)."""
        used = {s for a in doc.agents.values() for s in a.skills}
        current = {r.name: r.sha for r in self.imported()}
        return {n: current.get(n) or doc.skill_pins[n] for n in sorted(used) if n in current or n in doc.skill_pins}

    def pending_updates(self, doc: WorkflowDoc) -> dict[str, str]:
        """Skills whose installed commit differs from the pin in `doc` (name -> new sha).
        They take effect when the workflow is published again."""
        new = self.pins_for(doc)
        return {n: sha for n, sha in new.items() if doc.skill_pins.get(n) != sha}

    def materialise(self, pins: dict[str, str]) -> Path | None:
        """Write the pinned skills as a local plugin; content-addressed, so it is
        written once and shared by every run with the same pins."""
        if not pins:
            return None
        key = hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()[:16]
        root = self.cache_dir / key
        if (root / ".complete").exists():
            return root
        tmp = self.cache_dir / f".{key}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / ".claude-plugin").mkdir(parents=True)
        (tmp / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": IMPORTED_PLUGIN, "version": "0.0.0", "description": "Skills imported from GitHub"})
        )
        for name, sha in pins.items():
            rec = self.get(name, sha)
            for rel, content in rec.files.items():
                dest = (tmp / "skills" / name / rel).resolve()
                if not dest.is_relative_to((tmp / "skills" / name).resolve()):
                    raise SkillError(f"unsafe path '{rel}' in skill '{name}'")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
        (tmp / ".complete").write_text(json.dumps(pins))
        shutil.rmtree(root, ignore_errors=True)
        tmp.rename(root)
        return root
