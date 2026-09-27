"""AgentRunner seam + the Claude Agent SDK implementation.

Each station agent is a fresh, narrowly-scoped Claude session built from its
AgentSpec (see workflow.py):
  * the spec's prompt + the shared station contract (+ skill_prompts overlay)
  * the tools of the spec's preset only; PreToolUse hooks enforce guardrails
  * skills loaded from the factory plugin (plugin/), filtered per spec
  * factory actions exposed as in-process MCP tools (shared actions pattern)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agent_factory.agents.hooks import build_hooks

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]

PLUGIN_NAME = "agent-factory"
IMPORTED_PLUGIN = "imported-skills"  # see agent_factory.skills


@dataclass
class AgentRequest:
    run_id: str
    station: str
    role: str  # agent spec id
    prompt: str
    cwd: Path
    model: str
    system_prompt: str = ""  # the spec's prompt body
    tools: list[str] = field(default_factory=lambda: ["Read", "Glob", "Grep"])
    observe_only: bool = False
    produces: list[str] = field(default_factory=list)  # files the spec promises (checked by the engine)
    max_turns: int = 50
    skills: list[str] = field(default_factory=list)  # built-in skills (factory plugin)
    imported_skills: list[str] = field(default_factory=list)  # pinned imports, loaded from imported_plugin
    imported_plugin: Path | None = None
    skill_overlay: str = ""
    output_schema: dict[str, Any] | None = None
    protected_paths: list[str] = field(default_factory=list)  # e.g. holdout dir
    readable_extra_dirs: list[Path] = field(default_factory=list)
    subagent_model: str = "haiku"  # efficient-frontier: cheap model for mechanical work


@dataclass
class AgentResult:
    ok: bool
    text: str = ""
    structured: Any = None
    cost_usd: float = 0.0
    turns: int = 0
    error: str | None = None
    rate_limited: bool = False
    limit_utilization: float | None = None
    limit_resets_at: int | None = None


def plugins_for(factory_home: Path, req: AgentRequest) -> list[dict[str, str]]:
    plugins = [{"type": "local", "path": str(factory_home / "plugin")}]
    if req.imported_skills and req.imported_plugin:
        plugins.append({"type": "local", "path": str(req.imported_plugin)})
    return plugins


def init_summary(data: dict[str, Any], req: AgentRequest) -> dict[str, Any]:
    """What the CLI actually loaded, from its init message: makes a missing
    plugin or skill visible in the run log instead of silently absent."""
    plugins = [p.get("name", "?") if isinstance(p, dict) else str(p) for p in data.get("plugins") or []]
    # newer CLIs report "skills"; older ones only list them among slash commands
    loaded = [str(s) for s in data.get("skills") or data.get("slash_commands") or []]
    wanted = skill_refs(req)
    missing = [w for w in wanted if loaded and w not in loaded and w.split(":", 1)[-1] not in loaded]
    seen = f"available {loaded}" if loaded else "not reported by the CLI"
    text = f"agent loaded plugins {plugins or '[]'}; requested skills {wanted}; {seen}"
    if missing:
        text += f"; MISSING {missing}"
    if "Skill" not in (data.get("tools") or []) and wanted:
        text += "; Skill tool NOT available"
    return {"text": text, "plugins": plugins, "skills": loaded, "wanted": wanted, "missing": missing}


def sdk_tools(req: AgentRequest) -> list[str]:
    """Built-in tools available to the agent. `tools` is the SDK's *base set*:
    without the Skill tool in it, no skill can ever be loaded, however it is
    listed in `skills`."""
    tools = list(req.tools)
    if skill_refs(req) and "Skill" not in tools:
        tools.append("Skill")
    return tools


def skill_refs(req: AgentRequest) -> list[str]:
    refs = [f"{PLUGIN_NAME}:{s}" for s in req.skills]
    if req.imported_plugin:
        refs += [f"{IMPORTED_PLUGIN}:{s}" for s in req.imported_skills]
    return refs


class AgentRunner(Protocol):
    async def run(self, req: AgentRequest, sink: EventSink) -> AgentResult: ...


class ClaudeAgentRunner:
    def __init__(self, factory_home: Path, tools_server: Any | None = None) -> None:
        self.factory_home = factory_home
        self.tools_server = tools_server  # in-process MCP server with factory actions

    def _system_prompt(self, req: AgentRequest) -> str:
        contract = (self.factory_home / "prompts" / "_contract.md").read_text()
        parts = [req.system_prompt, contract]
        if req.skill_overlay:
            parts.append(req.skill_overlay)
        parts.append(f"Run id: {req.run_id}. Station: {req.station}. Working dir: {req.cwd}.")
        return "\n\n".join(parts)

    async def run(self, req: AgentRequest, sink: EventSink) -> AgentResult:
        from claude_agent_sdk import (
            AgentDefinition,
            AssistantMessage,
            ClaudeAgentOptions,
            RateLimitEvent,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        tools = sdk_tools(req)
        allowed = list(req.tools)  # the SDK adds Skill(<name>) for each listed skill
        mcp_servers: dict[str, Any] = {}
        if self.tools_server is not None:
            mcp_servers["factory"] = self.tools_server
            allowed.append("mcp__factory__log_decision")

        subagents = {}
        if "Task" in tools:
            subagents["mechanic"] = AgentDefinition(
                description="Cheap helper for mechanical, well-specified edits: boilerplate tests, "
                "renames, formatting and lint fixes. Give it exact files and the expected result.",
                prompt="Make exactly the requested mechanical change inside the current directory, "
                "run the named check, and report the files changed and the check result.",
                tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
                model=req.subagent_model,
            )

        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt(req),
            model=req.model,
            cwd=str(req.cwd),
            tools=tools,
            allowed_tools=allowed,
            permission_mode="acceptEdits",
            max_turns=req.max_turns,
            agents=subagents or None,
            mcp_servers=mcp_servers,
            hooks=build_hooks(req.role, req.cwd, req.protected_paths, req.observe_only),
            plugins=plugins_for(self.factory_home, req),
            skills=skill_refs(req),
            setting_sources=["project"],  # product CLAUDE.md -> AGENTS.md conventions
            add_dirs=[str(d) for d in req.readable_extra_dirs],
            output_format=({"type": "json_schema", "schema": req.output_schema} if req.output_schema else None),
        )

        result = AgentResult(ok=False)
        try:
            async for msg in query(prompt=req.prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            await sink("agent", {"text": block.text[:2000]})
                        elif isinstance(block, ToolUseBlock):
                            await sink("tool", {"tool": block.name, "input": _brief(block.input)})
                elif isinstance(msg, RateLimitEvent):
                    info = msg.rate_limit_info
                    result.limit_utilization = info.utilization
                    result.limit_resets_at = info.resets_at
                    if info.status == "rejected":
                        result.rate_limited = True
                    await sink(
                        "rate_limit",
                        {"status": info.status, "utilization": info.utilization, "resets_at": info.resets_at},
                    )
                elif isinstance(msg, SystemMessage) and msg.subtype == "init":
                    await sink("agent_init", init_summary(msg.data, req))
                elif isinstance(msg, ResultMessage):
                    result.cost_usd = msg.total_cost_usd or 0.0
                    result.turns = msg.num_turns
                    result.text = msg.result or ""
                    result.structured = msg.structured_output
                    result.ok = not msg.is_error
                    if msg.is_error:
                        result.error = f"{msg.subtype}: {'; '.join(msg.errors or [])}"
        except Exception as exc:  # noqa: BLE001 - surfaced as a HOLD, never success
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"
        if req.output_schema and result.ok and result.structured is None:
            result.structured = _try_json(result.text)
            if result.structured is None:
                result.ok = False
                result.error = "agent returned no structured output (missing evidence)"
        return result


def _brief(tool_input: dict[str, Any]) -> str:
    for key in ("command", "file_path", "pattern", "path", "description"):
        if key in tool_input:
            return str(tool_input[key])[:300]
    return json.dumps(tool_input)[:300]


def _try_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None
