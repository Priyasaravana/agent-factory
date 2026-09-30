"""Agent observability (ADR-0022): what every agent call did, as run evidence.

Each agent call is wrapped by `AgentCall`:
- **Guardrail denials** become decision events (`denied` data): who tried what,
  and why it was refused. A refusal is evidence, not just something the agent saw.
- **One summary per call** (`agent_call` data): station, role, model, turns,
  duration, cost, tool calls by tool, denials, outcome, transcript file.
- **A transcript per call** under `artifacts/<run>/sessions/`: the agent's text,
  every tool call with its full input, every tool result, denials and the final
  result, one JSON object per line. Secrets are redacted (REDACTOR) and each
  entry and file is size-capped.

The hidden scenarios never reach a builder's transcript: holdout paths are
protected for every agent that is not the verifier (see StationContext.agent).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agent_factory.agents.runner import AgentResult, EventSink
from agent_factory.models import EventKind
from agent_factory.secret_refs import REDACTOR

MAX_TRANSCRIPT_BYTES = 2_000_000
TRANSCRIPT_NAME = re.compile(r"^[0-9]{2,4}-[a-z0-9_-]{1,60}-[a-z0-9_-]{1,60}\.jsonl$")

Emit = Callable[[EventKind, str, dict[str, Any]], Awaitable[None]]


def sessions_dir(artifacts: Path) -> Path:
    return artifacts / "sessions"


class AgentCall:
    def __init__(self, artifacts: Path, station: str, role: str, model: str, emit: Emit) -> None:
        self.station, self.role, self.model, self.emit = station, role, model, emit
        d = sessions_dir(artifacts)
        d.mkdir(parents=True, exist_ok=True)
        seq = len(list(d.glob("*.jsonl"))) + 1
        self.name = f"{seq:02d}-{_slug(station)}-{_slug(role)}.jsonl"
        self.path = d / self.name
        self.tools: Counter[str] = Counter()
        self.denied: list[dict[str, str]] = []
        self.bytes = 0
        self.truncated = False
        self.started = time.monotonic()

    def _write(self, entry: dict[str, Any]) -> None:
        if self.truncated:
            return
        line = REDACTOR.text(json.dumps({"t": round(time.monotonic() - self.started, 2), **entry}, default=str))
        if self.bytes + len(line) + 1 > MAX_TRANSCRIPT_BYTES:
            self.truncated = True
            line = json.dumps({"type": "truncated", "note": f"transcript capped at {MAX_TRANSCRIPT_BYTES} bytes"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.bytes += len(line) + 1

    def wrap(self, inner: EventSink) -> EventSink:
        """A sink that records everything and forwards what belongs in the event log."""

        async def sink(kind: str, data: dict[str, Any]) -> None:
            if kind == "transcript":
                self._write(data)
                return
            if kind == "denied":
                d = {
                    "station": self.station,
                    "role": self.role,
                    "tool": str(data.get("tool", "")),
                    "input": str(data.get("input", "")),
                    "reason": str(data.get("reason", "")),
                }
                self.denied.append(d)
                await self.emit(
                    EventKind.decision,
                    f"guardrail denied {d['tool']} for {self.role}: {d['reason']}",
                    {"denied": d},
                )
                return
            if kind == "tool":
                self.tools[str(data.get("tool", "?"))] += 1
            await inner(kind, data)

        return sink

    async def finish(self, res: AgentResult) -> dict[str, Any]:
        summary = {
            "station": self.station,
            "role": self.role,
            "model": self.model,
            "ok": res.ok,
            "error": (res.error or "")[:500],
            "turns": res.turns,
            "duration_s": round(time.monotonic() - self.started, 1),
            "cost_usd": round(res.cost_usd, 4),
            "tool_calls": sum(self.tools.values()),
            "tools": dict(self.tools.most_common()),
            "denied": len(self.denied),
            "transcript": self.name if self.path.exists() else None,
            "transcript_truncated": self.truncated,
        }
        await self.emit(
            EventKind.log,
            f"agent call: {self.role} ({self.model}) {res.turns} turns, {_dur(summary['duration_s'])}, "
            f"${summary['cost_usd']:.2f}, {summary['tool_calls']} tool calls"
            + (f", {len(self.denied)} denied" if self.denied else ""),
            {"agent_call": summary},
        )
        return summary


def read_transcript(artifacts: Path, name: str, limit: int = 5000) -> list[dict[str, Any]]:
    """Entries of one transcript. `name` must be a transcript file name (no paths)."""
    if not TRANSCRIPT_NAME.match(name):
        raise ValueError("not a transcript name")
    path = sessions_dir(artifacts) / name
    if not path.is_file():
        raise FileNotFoundError(name)
    out = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                out.append({"type": "truncated", "note": f"showing the first {limit} entries"})
                break
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-")[:60] or "x"


def _dur(s: float) -> str:
    return f"{s:.0f}s" if s < 60 else f"{int(s // 60)}m{int(s % 60):02d}s"
