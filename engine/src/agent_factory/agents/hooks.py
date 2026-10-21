"""PreToolUse guardrails. Pure functions (`evaluate`) so they are unit-tested
without a model; `build_hooks` adapts them to the Agent SDK hook API."""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# Never allowed, for any role.
DENY_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|~|\$HOME)(\s|$)", "recursive delete of root/home"),
    (r"\bsudo\b", "privilege escalation"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh\b", "piping remote scripts to a shell"),
    (r"\bgit\s+push\b", "only the engine publishes (Handover station)"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s+\.)", "destructive git operation"),
    (r"\bdocker\s+(system|volume|image)\s+prune\b", "destructive docker prune"),
    (r"\bkind\s+delete\b", "deleting the cluster"),
    (r"\bkubectl\b[^\n]*\bdelete\b[^\n]*\b(ns|namespace|node|crd|clusterrole)", "cluster-scoped delete"),
    (r"\bkubectl\b[^\n]*(-n|--namespace)[= ]?kube-system", "touching kube-system"),
    (r"(^|\s)(env|printenv)(\s|$)", "dumping environment secrets"),
    (
        r"CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_CUSTOM_HEADERS|GITHUB_TOKEN",
        "reading credentials",
    ),
    (r"/certs/", "reading Docker TLS client keys"),
]

# The verifier may only observe: read files, call the running app.
VERIFIER_BASH_PREFIXES = (
    "curl",
    "wget",
    "jq",
    "cat",
    "ls",
    "grep",
    "head",
    "tail",
    "sleep",
    "echo",
    "python3 -c",
    "kubectl get",
    "kubectl logs",
    "kubectl describe",
    # read-only git, for reviewers reading the iteration's change
    "git diff",
    "git log",
    "git show",
    "git status",
    "git ls-files",
    "git blame",
    # reading helpers (ADR-0043 §6: Guestbook's reviewer was denied 16 of these); each is checked
    # below for the options that would let it write
    "cd",
    "pwd",
    "sed",
    "sort",
    "uniq",
    "wc",
    "cut",
    "find",
)

_SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;"}
_OUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
_FIND_WRITES = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
# sed scripts that only print: a line range (`66,100p`), a pattern (`/x/p`), or a substitution
# without the `w` (write) or `e` (execute) flags
_SED_PRINT = re.compile(r"^(?:(?:\d+|\$)(?:,(?:\d+|\$))?|/(?:[^/\\]|\\.)*/(?:,/(?:[^/\\]|\\.)*/)?)p$")
_SED_SUB = re.compile(r"^s(?P<d>[^\w\s\\])(?:(?!(?P=d))[^\\]|\\.)*(?P=d)(?:(?!(?P=d))[^\\]|\\.)*(?P=d)[gpiI0-9]*$")


def _segments(cmd: str) -> list[list[str]] | None:
    """Split a command line into its steps, the way a shell does: a quoted `\\|` or `;` inside a
    grep pattern is not a pipe. None when it can't be parsed (unbalanced quotes)."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        return None
    segs: list[list[str]] = [[]]
    for t in tokens:
        if t in _SEPARATORS:
            segs.append([])
        else:
            segs[-1].append(t)
    return [s for s in segs if s]


def _sed_reads_only(args: list[str]) -> bool:
    scripts, rest, it = [], [], iter(args)
    for a in it:
        if a in ("-n", "--quiet", "--silent", "-E", "-r", "--regexp-extended"):
            continue
        if a in ("-e", "--expression"):
            scripts.append(next(it, ""))
        elif a.startswith("-"):
            return False  # -i (in place), -f (script file), -s, … : not observing
        else:
            rest.append(a)
    if not scripts and rest:
        scripts.append(rest.pop(0))
    return bool(scripts) and all(_SED_PRINT.match(x) or _SED_SUB.match(x) for x in scripts)


def _observe_denial(role: str, cmd: str) -> str | None:
    """Why an observe-only agent may not run this, or None."""
    segs = _segments(cmd)
    if segs is None:
        return f"'{role}' is observe-only; the command could not be parsed"
    if "$(" in cmd or "`" in cmd:
        return f"'{role}' is observe-only; command substitution is not allowed"
    for words in segs:
        argv: list[str] = []
        i = 0
        while i < len(words):
            w, nxt = words[i], words[i + 1] if i + 1 < len(words) else ""
            if w.isdigit() and nxt in _OUT_REDIRECTS | {">&", "<"}:
                i += 1  # the `2` of `2>/dev/null`
                continue
            if w in _OUT_REDIRECTS:
                if nxt != "/dev/null":
                    return f"'{role}' is observe-only; writing output to a file is not allowed"
                i += 2
                continue
            if w in (">&", "<"):
                i += 2  # 2>&1 joins streams; < reads a file
                continue
            argv.append(w)
            i += 1
        if argv[:2] == ["git", "-C"] and len(argv) > 3:
            argv = ["git", *argv[3:]]  # reading another checkout's history is still reading
        line = " ".join(argv)
        if not line:
            continue
        if not any(line == p or line.startswith(p + " ") for p in VERIFIER_BASH_PREFIXES):
            return f"'{role}' is observe-only; '{line[:60]}' is not allowed"
        tool, args = argv[0], argv[1:]
        if tool == "git" and any(re.match(r"^(--output|-o)(=|$)", a) for a in args):
            return f"'{role}' is observe-only; git output to a file is not allowed"
        if tool == "sed" and not _sed_reads_only(args):
            return f"'{role}' is observe-only; sed may only print (-n with a range or pattern, or s///)"
        if tool == "sort" and any(re.match(r"^(-o|--output)", a) or re.match(r"^-[a-zA-Z]*o", a) for a in args):
            return f"'{role}' is observe-only; sort -o writes a file"
        if tool == "uniq" and len([a for a in args if not a.startswith("-")]) > 1:
            return f"'{role}' is observe-only; uniq with an output file is not allowed"
        if tool == "find" and any(a in _FIND_WRITES for a in args):
            return f"'{role}' is observe-only; find may only list"
    return None


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def evaluate(
    role: str,
    tool_name: str,
    tool_input: dict[str, Any],
    cwd: Path,
    protected_paths: list[str],
    observe_only: bool = False,
) -> str | None:
    """Return a denial reason, or None to allow. `observe_only` comes from the
    agent spec's tool preset (observer/reviewer), not from the agent's name."""
    blob = " ".join(str(v) for v in tool_input.values())

    for p in protected_paths:
        if p and p in blob:
            return f"access to protected path '{p}' is not allowed for role '{role}'"

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        for pattern, why in DENY_PATTERNS:
            if re.search(pattern, cmd):
                return f"blocked: {why}"
        if observe_only:
            return _observe_denial(role, cmd)
        return None

    if tool_name in WRITE_TOOLS:
        if observe_only:
            return f"'{role}' is observe-only and cannot modify files"
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        try:
            resolved = (cwd / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        except (OSError, ValueError):
            return "unresolvable write path"
        if not resolved.is_relative_to(cwd.resolve()):
            return f"writes are confined to the run worktree ({cwd})"
    return None


OnDeny = Callable[[str, dict[str, Any], str], Awaitable[None]]


def build_hooks(
    role: str,
    cwd: Path,
    protected_paths: list[str],
    observe_only: bool = False,
    on_deny: OnDeny | None = None,
) -> dict[str, Any]:
    """PreToolUse guardrails. Every denial is reported through `on_deny` (tool,
    input, reason) so it becomes run evidence (ADR-0022), not just a refusal the
    agent sees."""
    from claude_agent_sdk import HookMatcher

    async def guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        tool, tool_input = input_data.get("tool_name", ""), input_data.get("tool_input", {}) or {}
        reason = evaluate(role, tool, tool_input, cwd, protected_paths, observe_only)
        if reason is None:
            return {}
        if on_deny is not None:
            await on_deny(tool, tool_input, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[guard])]}
