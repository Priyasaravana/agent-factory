"""What a failed test run says, kept apart from what the app printed (UX-13). Pure.

A test command's output mixes the runner's report with the server's logs (a preview's sign-in
lines, request logs). Its tail can be all server logs, so the hold showed no failing assertion.
`decisive` picks the runner's own lines out of the complete output: Playwright's numbered failure
blocks and its counts, pytest's `FAILED` lines, assertion lines and summary. A hold leads with
them, then the tail, and links the complete output saved in the change's evidence.
"""

from __future__ import annotations

import re

# Playwright (list/line reporters): "  1) [chromium] › e2e/x.spec.ts:10:5 › @R1 title ───"
_PW_FAILURE = re.compile(r"^\s*\d+\) (\[[^\]]+\] )?\S.*›")
# counts at the end of a run: Playwright "  2 failed", "5 passed (12.3s)"; pytest "== 1 failed, 5 passed in 2s =="
_COUNTS = re.compile(r"^[\s=]*\d+ (failed|passed|flaky|skipped|errors?|did not run|interrupted)\b")
_PYTEST = re.compile(r"^(FAILED|ERROR) \S+|^E\s{2,}\S|^=+ (short test summary info|FAILURES|ERRORS) =+")
BLOCK_LINES = 18  # lines kept after a Playwright failure header: the error, the expect, the code frame
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _in_block(line: str) -> bool:
    """A Playwright failure block's body is indented (or blank); the next failure, the counts and a
    server's log line (unindented: `[WebServer] …`) end it."""
    if _PW_FAILURE.match(line) or _COUNTS.match(line):
        return False
    return not line.strip() or line[:1].isspace()


def decisive(text: str, limit: int = 3000) -> str:
    """The runner's failure report from a test command's complete output, in order, at most
    `limit` characters; "" when it holds none a runner wrote (then only the tail can be shown)."""
    lines = _ANSI.sub("", text).splitlines()
    keep: list[int] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _PW_FAILURE.match(line):
            end = i + 1
            while end < len(lines) and end - i <= BLOCK_LINES and _in_block(lines[end]):
                end += 1
            keep.extend(range(i, end))
            i = end
            continue
        if _COUNTS.match(line) or _PYTEST.match(line):
            keep.append(i)
        i += 1
    picked: list[str] = []
    size = 0
    for n in dict.fromkeys(keep):
        line = lines[n].rstrip()
        if size + len(line) + 1 > limit:
            picked.append("…")
            break
        picked.append(line)
        size += len(line) + 1
    return "\n".join(picked).strip("\n")


def failure_detail(command: str, returncode: int, whole: str, tail: str, saved: str | None) -> str:
    """A failed test command for a hold or a repair: the failing tests first, then the output's
    tail, then where the complete output is kept."""
    report = decisive(whole)
    parts = [f"`{command}` exited {returncode}:"]
    if report:
        parts.append(f"Failing tests (from the complete output):\n{report}")
        parts.append(f"Last lines of the output:\n{tail[-2500:]}")
    else:
        parts.append(tail[-4000:])
    if saved:
        parts.append(f"Complete output: {saved}")
    return "\n\n".join(parts)
