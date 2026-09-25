"""PreToolUse guardrails. Pure functions (`evaluate`) so they are unit-tested
without a model; `build_hooks` adapts them to the Agent SDK hook API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Never allowed, for any role.
DENY_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|~|\$HOME)(\s|$)", "recursive delete of root/home"),
    (r"\bsudo\b", "privilege escalation"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh\b", "piping remote scripts to a shell"),
    (r"\bgit\s+push\b", "only the engine publishes (Deliver station)"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s+\.)", "destructive git operation"),
    (r"\bdocker\s+(system|volume|image)\s+prune\b", "destructive docker prune"),
    (r"\bkind\s+delete\b", "deleting the cluster"),
    (r"\bkubectl\b[^\n]*\bdelete\b[^\n]*\b(ns|namespace|node|crd|clusterrole)", "cluster-scoped delete"),
    (r"\bkubectl\b[^\n]*(-n|--namespace)[= ]?kube-system", "touching kube-system"),
    (r"(^|\s)(env|printenv)(\s|$)", "dumping environment secrets"),
    (r"CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|GITHUB_TOKEN", "reading credentials"),
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
)

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def evaluate(
    role: str,
    tool_name: str,
    tool_input: dict[str, Any],
    cwd: Path,
    protected_paths: list[str],
) -> str | None:
    """Return a denial reason, or None to allow."""
    blob = " ".join(str(v) for v in tool_input.values())

    for p in protected_paths:
        if p and p in blob:
            return f"access to protected path '{p}' is not allowed for role '{role}'"

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        for pattern, why in DENY_PATTERNS:
            if re.search(pattern, cmd):
                return f"blocked: {why}"
        if role == "verifier":
            for part in re.split(r"&&|\|\||;|\|", cmd):
                part = part.strip()
                if part and not part.startswith(VERIFIER_BASH_PREFIXES):
                    return f"verifier is observe-only; '{part[:60]}' is not allowed"
        return None

    if tool_name in WRITE_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        try:
            resolved = (cwd / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        except (OSError, ValueError):
            return "unresolvable write path"
        if not resolved.is_relative_to(cwd.resolve()):
            return f"writes are confined to the run worktree ({cwd})"
    return None


def build_hooks(role: str, cwd: Path, protected_paths: list[str]) -> dict[str, Any]:
    from claude_agent_sdk import HookMatcher

    async def guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        reason = evaluate(
            role,
            input_data.get("tool_name", ""),
            input_data.get("tool_input", {}) or {},
            cwd,
            protected_paths,
        )
        if reason is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[guard])]}
