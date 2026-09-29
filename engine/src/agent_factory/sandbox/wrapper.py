"""`agent-factory-sandbox-claude`: the Claude CLI, but inside a sandbox.

The Agent SDK spawns this instead of the bundled CLI (ClaudeAgentOptions.cli_path)
and talks to it over stdin/stdout exactly as before: hooks, the in-process MCP
tools and structured output all travel over that stream, so they keep working.
This process only replaces itself with `docker run -i … claude <same args>`.

The container spec comes from the JSON file named by AF_SANDBOX_SPEC, written
by the engine for this one agent session.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_factory.sandbox.manager import cli_version
from agent_factory.sandbox.spec import SandboxSpec, forwarded_env_names

SPEC_ENV = "AF_SANDBOX_SPEC"


def build_argv(spec_json: str, cli_args: list[str], env: dict[str, str]) -> list[str]:
    spec = SandboxSpec.from_json(spec_json)
    spec.pass_env = forwarded_env_names(env)
    return spec.argv(["claude", *cli_args], interactive=True)


def main() -> None:
    args = sys.argv[1:]
    if args in (["-v"], ["--version"]):
        # the SDK's version probe: the image carries this exact CLI version
        print(f"{cli_version()} (Claude Code)")
        return
    path = os.environ.get(SPEC_ENV)
    if not path or not Path(path).exists():
        print(f"{SPEC_ENV} is not set to a sandbox spec file; refusing to run unsandboxed", file=sys.stderr)
        sys.exit(2)
    argv = build_argv(Path(path).read_text(), args, dict(os.environ))
    os.execvp(argv[0], argv)  # noqa: S606 - fixed program, arguments are an argv list


if __name__ == "__main__":
    main()
