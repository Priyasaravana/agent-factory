"""Cut-off output as a class (roadmap "Now" item 2): the executor keeps the complete output, marks a
result `truncated`, and code that decides on what a command printed reads it whole or refuses it."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_factory import run_report
from agent_factory.engine import design_station, stations
from agent_factory.executor import TAIL_CHARS, CommandResult, LocalExecutor, OutputCutOff, finished
from agent_factory.models import ProductTarget

SRC = Path(__file__).resolve().parents[1] / "src" / "agent_factory"


# ----------------------------------------------------------------- executor --
async def test_large_output_is_kept_whole_and_marked_truncated():
    script = "import sys; [print(f'line-{i:05d}') for i in range(3000)]"
    res = await LocalExecutor().run(f"{sys.executable} -c {script!r}")
    assert res.ok and res.truncated
    assert len(res.output) == TAIL_CHARS and res.output.endswith("line-02999\n")
    assert "line-00000" not in res.output  # the tail is for people…
    assert res.lines()[0] == "line-00000" and len(res.lines()) == 3000  # …decisions read it all


async def test_small_output_is_not_truncated():
    res = await LocalExecutor().run("echo hello")
    assert not res.truncated and res.whole() == res.output == "hello\n"


def test_a_result_with_only_its_tail_refuses_a_decision():
    res = CommandResult("helm template app chart", 0, "x" * TAIL_CHARS, truncated=True)
    with pytest.raises(OutputCutOff, match="cut off"):
        res.whole()
    assert finished("c", 0, "y" * (TAIL_CHARS + 1)).whole() == "y" * (TAIL_CHARS + 1)


# ------------------------------------------- L-1: files the change touched --
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _ctx(repo: Path) -> SimpleNamespace:
    ex = LocalExecutor()

    async def git(args: str, cwd: Path) -> CommandResult:
        return await ex.run(f"git {args}", cwd=cwd)

    product = SimpleNamespace(target=ProductTarget.repo, repo_ref="main")
    return SimpleNamespace(ws=SimpleNamespace(git=git), worktree=repo, product=product)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


async def test_a_change_past_the_tail_scans_every_file_it_touched(repo: Path):
    names = [f"src/module_{i:04d}/handler_with_a_long_name.py" for i in range(400)]  # ~20 kB of names
    for n in names:
        (repo / n).parent.mkdir(parents=True, exist_ok=True)
        (repo / n).write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "big")
    assert sum(len(n) + 1 for n in names) > TAIL_CHARS
    assert await stations._changed_files(_ctx(repo)) == sorted(names)


async def test_a_listing_git_cannot_give_stops_rather_than_scanning_nothing(repo: Path):
    ctx = _ctx(repo)
    ctx.product.repo_ref = "no-such-branch"
    with pytest.raises(RuntimeError, match="could not list the files"):
        await stations._changed_files(ctx)


# --------------------------------------------- L-2: the design-doc guard --
async def test_the_design_guard_undoes_every_file_past_the_tail(repo: Path):
    ctx = _ctx(repo)
    stray = [f"src/generated/a_rather_long_file_name_{i:04d}.ts" for i in range(300)]
    for n in stray:
        (repo / n).parent.mkdir(parents=True, exist_ok=True)
        (repo / n).write_text("export {}\n")
    undone = await design_station._keep_to_design_docs(ctx)
    assert undone == sorted(stray)
    assert not (repo / "src").exists() or not any((repo / "src").rglob("*.ts"))


async def test_the_design_guard_stops_when_it_cannot_read_the_worktree(tmp_path: Path):
    with pytest.raises(RuntimeError, match="worktree's status"):
        await design_station._keep_to_design_docs(_ctx(tmp_path))  # not a git repo


# ------------------------------------------------ UX-13: failing tests first --
_PLAYWRIGHT = """
Running 6 tests using 1 worker
[WebServer] GET /api/session 200 in 12ms
  1) [chromium] › e2e/expenses.spec.ts:14:7 › @R3 exports claims as CSV ────────────

    Error: expect(received).toContain(expected)

    Expected substring: "amount,currency"
    Received string:    "date,title"

      18 |     expect(csv).toContain("amount,currency");

  1 failed
    [chromium] › e2e/expenses.spec.ts:14:7 › @R3 exports claims as CSV
  5 passed (14.2s)
"""


def test_the_failing_assertion_is_picked_out_of_server_logs():
    noise = "".join(f"[WebServer] POST /api/auth/sign-in 200 in {i}ms user=admin\n" for i in range(400))
    whole = _PLAYWRIGHT + noise  # the server kept logging after the report: the tail is all sign-in lines
    tail = whole[-TAIL_CHARS:]
    assert "toContain" not in tail
    report = run_report.decisive(whole)
    assert "@R3 exports claims as CSV" in report and 'Expected substring: "amount,currency"' in report
    assert "1 failed" in report and "5 passed" in report and "sign-in" not in report
    detail = run_report.failure_detail("make e2e", 1, whole, tail, "artifacts/c1/e2e-output.log")
    assert detail.index("Failing tests") < detail.index("Last lines") < detail.index("Complete output")


def test_pytest_failures_are_picked_too():
    whole = "collected 3 items\n...\nE   assert 409 == 500\n=== short test summary info ===\n"
    whole += "FAILED tests/test_users.py::test_duplicate_email - assert 409 == 500\n"
    whole += "=== 1 failed, 2 passed in 1.1s ===\n"
    report = run_report.decisive(whole)
    assert "FAILED tests/test_users.py::test_duplicate_email" in report and "E   assert 409 == 500" in report


def test_output_without_a_runner_report_shows_the_tail():
    assert run_report.decisive("make: *** [verify] Error 2\n") == ""
    out = "make: *** [verify] Error 2"
    assert "Error 2" in run_report.failure_detail("make verify", 2, out, out, None)


# -------------------------------------- the guard: decisions never read `.output` --
# A read of `.output` other than its tail for display (`x.output[-N:]`, `x.output.strip()[-N:]`) is a
# decision on cut-off output unless it is listed here with the reason it isn't one.
DISPLAY_ONLY = {
    ("executor.py", "whole"): "the method that returns the output when it was not cut off",
    ("engine/stations.py", "cmd"): "failure classes read the end on purpose: unknown is work (ADR-0043); "
    "the notes are appended for people",
    ("engine/stations.py", "_test_failure"): "the tail for display, the whole when it was kept",
    ("engine/stations.py", "committed_files"): "the last line of a failed listing, for the message",
    ("sandbox/manager.py", "_image"): "logged for people",
}


def _reads(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "output" and isinstance(node.ctx, ast.Load)):
            continue
        up = parents[node]
        if isinstance(up, ast.Attribute) and up.attr in {"strip", "rstrip"} and isinstance(parents[up], ast.Call):
            up = parents[parents[up]]
        if isinstance(up, ast.Subscript) and isinstance(up.slice, ast.Slice):
            lower = up.slice.lower
            if isinstance(lower, ast.UnaryOp) and isinstance(lower.op, ast.USub) and up.slice.upper is None:
                continue  # a tail, for display
        fn = next(
            (p.name for p in _ancestors(node, parents) if isinstance(p, ast.FunctionDef | ast.AsyncFunctionDef)), "?"
        )
        found.append((fn, node.lineno))
    return found


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node in parents:
        node = parents[node]
        yield node


def test_no_decision_reads_the_cut_off_output():
    offenders = []
    used = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        for fn, line in _reads(path):
            if (rel, fn) in DISPLAY_ONLY:
                used.add((rel, fn))
            else:
                offenders.append(f"{rel}:{line} in {fn}()")
    assert not offenders, "read `.whole()` / `.lines()` to decide, or slice the tail to show:\n" + "\n".join(offenders)
    assert used == set(DISPLAY_ONLY), f"stale entries: {set(DISPLAY_ONLY) - used}"
