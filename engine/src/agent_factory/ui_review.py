"""Look and feel (ADR-0044 §4): route screenshots as evidence, and a UI review that reports only.

Pure helpers:
- `parse_rubric`: the stack pack's written rubric (criteria scored 1–5);
- `collect`: copy the e2e suite's screenshots into the change's evidence, capped and named safely;
- `judge`: check the reviewer's scores against the rubric and summarise them.

The UI review never fails a change: blocking waits until the rubric is calibrated on known-good and
known-bad screenshots (ADR-0044, deferred). The engine still judges completeness, as every judging
station does (ADR-0020): the reviewer's report is evidence, not a verdict.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_SHOTS = 48  # per run: routes × 2 sizes × 2 schemes; more is a sign of a runaway suite
MAX_SHOT_BYTES = 4 * 1024 * 1024
SCORE_MIN, SCORE_MAX = 1, 5
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}\.png$")


class RubricError(ValueError):
    pass


@dataclass(frozen=True)
class Criterion:
    id: str
    title: str
    look_for: str


@dataclass(frozen=True)
class Rubric:
    criteria: tuple[Criterion, ...]

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.criteria]

    def prompt_block(self) -> str:
        return "\n".join(f"- `{c.id}` — {c.title}: {c.look_for}" for c in self.criteria)


def parse_rubric(raw: Any, where: str = "rubric") -> Rubric:
    """`criteria: [{id, title, look_for}]`, ids unique and slug-like."""
    items = raw.get("criteria") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise RubricError(f"{where}: `criteria` must be a non-empty list")
    out: list[Criterion] = []
    for i, c in enumerate(items):
        fields = ("id", "title", "look_for")
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c.get(k) for k in fields):
            raise RubricError(f"{where}: criterion {i + 1} needs id, title and look_for")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", c["id"]):
            raise RubricError(f"{where}: criterion id '{c['id']}' must be a lowercase slug")
        out.append(Criterion(c["id"], c["title"].strip(), " ".join(c["look_for"].split())))
    ids = [c.id for c in out]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise RubricError(f"{where}: criteria listed twice: {', '.join(dupes)}")
    return Rubric(tuple(out))


def collect(src: Path, dest: Path) -> list[str]:
    """Copy the PNG screenshots in `src` (flat) into `dest`, replacing what was there. Files with
    odd names, symlinks, oversized files and anything past the cap are skipped. Returns the names kept."""
    if dest.exists():
        shutil.rmtree(dest)
    if not src.is_dir():
        return []
    kept: list[str] = []
    for p in sorted(src.iterdir()):
        if len(kept) >= MAX_SHOTS:
            break
        name = p.name.lower()
        if p.is_symlink() or not p.is_file() or not _NAME.match(name) or p.stat().st_size > MAX_SHOT_BYTES:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest / name)
        kept.append(name)
    return kept


def schema(rubric: Rubric) -> dict[str, Any]:
    """What the UI reviewer returns: one score per criterion, findings with the screenshot they're about."""
    return {
        "type": "object",
        "required": ["scores", "findings", "summary"],
        "properties": {
            "summary": {"type": "string"},
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["criterion", "score", "why"],
                    "properties": {
                        "criterion": {"type": "string", "enum": rubric.ids},
                        "score": {"type": "integer", "minimum": SCORE_MIN, "maximum": SCORE_MAX},
                        "why": {"type": "string"},
                    },
                },
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["screenshot", "issue"],
                    "properties": {"screenshot": {"type": "string"}, "issue": {"type": "string"}},
                },
            },
        },
    }


@dataclass
class UiReview:
    scores: dict[str, int] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.problems

    @property
    def mean(self) -> float | None:
        return round(sum(self.scores.values()) / len(self.scores), 2) if self.scores else None

    def headline(self) -> str:
        if not self.complete:
            return "UI review incomplete (reported only): " + "; ".join(self.problems[:3])
        low = sorted(k for k, v in self.scores.items() if v <= 2)
        return f"UI review {self.mean}/5 (reported only)" + (f"; weak: {', '.join(low)}" if low else "")

    def as_data(self) -> dict[str, Any]:
        return {
            "scores": self.scores,
            "why": self.why,
            "mean": self.mean,
            "findings": self.findings,
            "summary": self.summary,
            "complete": self.complete,
            "problems": self.problems,
            "blocking": False,
        }


def judge(rubric: Rubric, report: Any, shots: list[str]) -> UiReview:
    """Every criterion scored once within 1–5; findings name a screenshot that exists."""
    out = UiReview()
    if not isinstance(report, dict):
        out.problems.append("no report")
        return out
    out.summary = str(report.get("summary", "")).strip()[:2000]
    for s in report.get("scores") or []:
        if not isinstance(s, dict):
            continue
        cid, score = s.get("criterion"), s.get("score")
        if cid not in rubric.ids:
            out.problems.append(f"unknown criterion '{cid}'")
        elif cid in out.scores:
            out.problems.append(f"'{cid}' scored twice")
        elif not isinstance(score, int) or isinstance(score, bool) or not SCORE_MIN <= score <= SCORE_MAX:
            out.problems.append(f"'{cid}' score must be {SCORE_MIN}–{SCORE_MAX}")
        else:
            out.scores[cid] = score
            out.why[cid] = str(s.get("why", "")).strip()[:500]
    if missing := [c for c in rubric.ids if c not in out.scores and not any(c in p for p in out.problems)]:
        out.problems.append("not scored: " + ", ".join(missing))
    known = set(shots)
    for f in report.get("findings") or []:
        if isinstance(f, dict) and f.get("issue"):
            shot = str(f.get("screenshot", "")).strip().split("/")[-1].lower()
            out.findings.append({"screenshot": shot if shot in known else "", "issue": str(f["issue"]).strip()[:500]})
    return out
