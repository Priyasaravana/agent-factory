"""Image findings as evidence (ADR-0042, pure): what the Build station's image scan found, who
can fix it, and what the change does about it.

The scan (Trivy, `scan_command`) reports CVEs in the built image. Each finding is classified by
where it came from:

- **app dependency**: a package the product's own lockfile pins. The developer can bump it, so
  the change goes back to develop with the exact package, installed version and fixed version.
- **base image**: an OS package, or a language package the product doesn't pin (npm inside the
  Node.js image, pip inside the Python image). Only a new base image fixes it, which is a pack
  decision, so the change holds for a person at once instead of looping the developer.

And by whether a fix exists:

- **no fix released**: nobody can fix it today, so the change holds until a person accepts the
  risk with an expiry on the product's work ledger (ADR-0040), or the vendor ships a fix.

Accepted risks (unexpired) don't block. A fix loop that leaves the same app findings behind
holds instead of looping again. Findings below the blocking severity are recorded, never block.
Nothing here does IO or calls a model; the station reads files and the store.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

SEVERITIES = ("critical", "high")  # what the factory reads from an image scan
RANK = {"critical": 0, "high": 1}
FAIL_ON = ("critical", "high")
MAX_LISTED = 25  # findings spelled out in a brief; counts cover all

Origin = Literal["app", "base"]
Action = Literal["pass", "fix", "hold"]

# where a language package lives when the base image put it there, not the app
_BASE_PATHS = ("usr/", "opt/", "bin/", "sbin/", "lib/", "var/lib/", "nodejs/")


class NotAnImageReport(ValueError):
    """The scanner's output isn't a Trivy image report (e.g. an old table-format command)."""


@dataclass(frozen=True)
class ImageFinding:
    cve: str
    package: str
    installed: str
    fixed: str  # "" when no fix is released
    severity: str  # critical | high
    origin: Origin
    kind: str  # os-pkgs | lang-pkgs
    ecosystem: str  # debian, alpine, node-pkg, python-pkg ...
    path: str = ""  # the package's file inside the image (language packages)
    title: str = ""

    @property
    def fixable(self) -> bool:
        return bool(self.fixed)

    @property
    def key(self) -> str:
        """Same finding across scans: the CVE in this package."""
        return f"image {self.cve} {self.package}".lower()

    def where(self) -> str:
        return "base image" if self.origin == "base" else "app dependency"

    def line(self) -> str:
        fix = f"→ fixed in {self.fixed}" if self.fixed else "(no fix released)"
        return f"{self.cve} ({self.severity}) in {self.package} {self.installed} {fix}"

    def as_data(self) -> dict[str, Any]:
        return {**self.__dict__, "fixable": self.fixable}


# ------------------------------------------------------------------ parse --
def locked_packages(files: Mapping[str, str]) -> set[str]:
    """Package names the product pins, from its committed lockfiles (file name → text):
    package-lock.json (npm), uv.lock and poetry.lock (Python). Names are lower-cased."""
    names: set[str] = set()
    for name, text in files.items():
        base = name.rsplit("/", 1)[-1]
        if base == "package-lock.json":
            try:
                doc = json.loads(text)
            except ValueError:
                continue
            for path in (doc.get("packages") or {}) if isinstance(doc, dict) else {}:
                if "node_modules/" in path:
                    names.add(path.rsplit("node_modules/", 1)[-1].lower())
            for dep in (doc.get("dependencies") or {}) if isinstance(doc, dict) else {}:
                names.add(str(dep).lower())  # lockfile v1
        elif base in ("uv.lock", "poetry.lock"):
            names.update(m.lower() for m in re.findall(r'^name\s*=\s*"([^"]+)"', text, re.M))
    return names | {_pep503(n) for n in names}


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _origin(kind: str, package: str, path: str, locked: set[str]) -> Origin:
    if kind != "lang-pkgs":
        return "base"  # OS packages only come with the base image
    if locked:
        return "app" if package.lower() in locked or _pep503(package) in locked else "base"
    return "base" if path.startswith(_BASE_PATHS) else "app"  # no lockfile: judge by where it lives


def parse(text: str, locked: Iterable[str] = ()) -> list[ImageFinding]:
    """Trivy's JSON image report → findings at high or above, classified by origin.
    Raises NotAnImageReport for anything else, so the station can say the scan wasn't read."""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise NotAnImageReport("not JSON: set scan_command to Trivy's `--format json`") from exc
    if not isinstance(doc, dict) or ("Results" not in doc and "SchemaVersion" not in doc):
        raise NotAnImageReport("not a Trivy image report")
    pinned = {p.lower() for p in locked}
    out: dict[tuple[str, str, str], ImageFinding] = {}
    for res in doc.get("Results") or []:
        if not isinstance(res, dict):
            continue
        kind, eco = str(res.get("Class") or ""), str(res.get("Type") or "")
        for v in res.get("Vulnerabilities") or []:
            if not isinstance(v, dict):
                continue
            sev = str(v.get("Severity") or "").lower()
            if sev not in RANK:
                continue
            pkg = str(v.get("PkgName") or "?")
            path = str(v.get("PkgPath") or "")
            f = ImageFinding(
                cve=str(v.get("VulnerabilityID") or "?"),
                package=pkg,
                installed=str(v.get("InstalledVersion") or ""),
                fixed=str(v.get("FixedVersion") or "").strip(),
                severity=sev,
                origin=_origin(kind, pkg, path, pinned),
                kind=kind,
                ecosystem=eco,
                path=path,
                title=str(v.get("Title") or "")[:200],
            )
            out.setdefault((f.cve, f.package, f.installed), f)  # one package at two paths: once
    return sorted(out.values(), key=lambda f: (RANK[f.severity], f.origin, f.package, f.cve))


def base_image(dockerfile: str) -> str:
    """The image the final stage builds FROM, following stage names back to a real image."""
    stages: dict[str, str] = {}
    last = ""
    for m in re.finditer(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", dockerfile, re.M | re.I):
        image, alias = m.group(1), m.group(2)
        image = stages.get(image.lower(), image)
        if alias:
            stages[alias.lower()] = image
        last = image
    return last


# ------------------------------------------------------------------ judge --
@dataclass
class Verdict:
    """What the Build station does with one scan."""

    action: Action
    fail_on: str
    findings: list[ImageFinding] = field(default_factory=list)  # high and above
    accepted: list[ImageFinding] = field(default_factory=list)  # blocking severity, risk accepted
    app_fixes: list[ImageFinding] = field(default_factory=list)  # the developer bumps these
    base_fixes: list[ImageFinding] = field(default_factory=list)  # a new base image fixes these
    no_fix: list[ImageFinding] = field(default_factory=list)  # a person accepts these, or waits
    repeated: bool = False  # the developer's fix left the same findings behind

    @property
    def recorded(self) -> list[ImageFinding]:
        """Below the blocking severity: kept in the ledger, never blocking."""
        return [f for f in self.findings if RANK[f.severity] > RANK[self.fail_on]]

    def headline(self) -> str:
        block = [*self.app_fixes, *self.base_fixes, *self.no_fix]
        parts = []
        if self.app_fixes:
            parts.append(f"{len(self.app_fixes)} to bump in the app")
        if self.base_fixes:
            parts.append(f"{len(self.base_fixes)} in the base image")
        if self.no_fix:
            parts.append(f"{len(self.no_fix)} with no fix released")
        head = (
            f"{len(block)} {self.fail_on} CVE(s): {', '.join(parts)}" if block else f"no blocking {self.fail_on} CVEs"
        )
        extra = []
        if self.accepted:
            extra.append(f"{len(self.accepted)} accepted risk")
        if self.recorded:
            extra.append(f"{len(self.recorded)} lower recorded")
        return f"image scan: {head}" + (f" ({', '.join(extra)})" if extra else "")

    def as_data(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return {
            "action": self.action,
            "fail_on": self.fail_on,
            "repeated": self.repeated,
            "counts": counts,
            "app_fixes": [f.key for f in self.app_fixes],
            "base_fixes": [f.key for f in self.base_fixes],
            "no_fix": [f.key for f in self.no_fix],
            "accepted": [f.key for f in self.accepted],
            "findings": [f.as_data() for f in self.findings],
        }


def judge(
    findings: list[ImageFinding],
    *,
    accepted: Iterable[str] = (),
    previous_fixes: Iterable[str] | None = None,
    fail_on: str = "critical",
) -> Verdict:
    """Pass, send to the developer, or hold for a person.

    - `accepted`: finding keys whose risk a person accepted (and that haven't expired).
    - `previous_fixes`: the app findings the last scan of this change sent to the developer;
      if none of them is gone, another loop won't help: hold.
    A person is needed first (base image, no fix): the change holds even if there are also app
    bumps, which are listed so the developer gets them after the person resumes."""
    if fail_on not in FAIL_ON:
        raise ValueError(f"fail_on must be one of {FAIL_ON}")
    ok = set(accepted)
    v = Verdict("pass", fail_on, list(findings))
    for f in findings:
        if RANK[f.severity] > RANK[fail_on]:
            continue
        if f.key in ok:
            v.accepted.append(f)
        elif not f.fixable:
            v.no_fix.append(f)
        elif f.origin == "base":
            v.base_fixes.append(f)
        else:
            v.app_fixes.append(f)
    if v.base_fixes or v.no_fix:
        v.action = "hold"
    elif v.app_fixes:
        before = set(previous_fixes or ())
        v.repeated = bool(before) and before <= {f.key for f in v.app_fixes}
        v.action = "hold" if v.repeated else "fix"
    return v


# ----------------------------------------------------------------- briefs --
def developer_brief(v: Verdict, lockfiles: Iterable[str] = ()) -> str:
    """What the developer agent gets: the exact bumps, and how to bump a transitive one."""
    locks = ", ".join(lockfiles) or "the lockfile"
    lines = [
        f"The image scan found {len(v.app_fixes)} {v.fail_on} CVE(s) in packages this app pins ({locks}).",
        "Bump each to at least the fixed version, regenerate the lockfile, and keep `make verify` green:",
    ]
    lines += [f"- {f.package}: {f.installed} → {f.fixed} ({f.cve})" for f in v.app_fixes[:MAX_LISTED]]
    if len(v.app_fixes) > MAX_LISTED:
        lines.append(f"- … and {len(v.app_fixes) - MAX_LISTED} more")
    lines += [
        "If a package isn't a direct dependency, upgrade the dependency that pulls it in, or pin it with",
        "the package manager's override (npm `overrides`, uv `override-dependencies`).",
        "Don't add scanner exclusions (.trivyignore): those are held for a person (change risk).",
    ]
    return "\n".join(lines)


def person_brief(v: Verdict, base: str = "") -> str:
    """Why the change holds, and what a person can do about it."""
    lines: list[str] = []
    if v.repeated:
        lines.append(
            "The developer's fix left the same CVE(s) in the image; another loop is unlikely to help."
            " Look at how the package gets in (a transitive dependency or the build), then resume:"
        )
        lines += [f"- {f.line()}" for f in v.app_fixes[:MAX_LISTED]]
    if v.base_fixes:
        src = f" ({base})" if base else ""
        lines.append(f"The base image{src} carries fixed CVE(s) the app can't bump:")
        lines += [f"- {f.line()}" for f in v.base_fixes[:MAX_LISTED]]
        lines.append(
            "The build pulls the base image's latest version, so its publisher hasn't shipped these fixes"
            " yet. Move to a patched base image (a new pack version, or the product's Dockerfile), or"
            " accept the risk with an expiry on the product's work ledger, then resume."
        )
    if v.no_fix:
        lines.append("No fix is released yet for:")
        lines += [f"- {f.line()} [{f.where()}]" for f in v.no_fix[:MAX_LISTED]]
        lines.append(
            "Accept the risk with an expiry on the product's work ledger (it reopens when it expires),"
            " or wait for a fix; then resume."
        )
    if v.app_fixes and not v.repeated:
        lines.append("After you resume, the developer bumps these app dependencies:")
        lines += [f"- {f.line()}" for f in v.app_fixes[:MAX_LISTED]]
    return "\n".join(lines)


def ledger_title(f: ImageFinding) -> str:
    return f"{f.cve} in {f.package} {f.installed} ({f.where()})"


def ledger_detail(f: ImageFinding) -> str:
    fix = f"fixed in {f.fixed}" if f.fixed else "no fix released yet"
    return f"{fix}. {f.title}".strip() if f.title else fix
