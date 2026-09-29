"""Agent sandbox (ADR-0014): agents and untrusted commands run in throw-away
containers with no secrets, no Docker access and allowlisted egress."""

from agent_factory.sandbox.manager import SandboxExecutor, SandboxManager
from agent_factory.sandbox.spec import SandboxSpec

__all__ = ["SandboxExecutor", "SandboxManager", "SandboxSpec"]
