"""Process settings from the environment (.env). Secrets live only here."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # live = real agents + real docker/kind; dry-run = fakes, zero model usage
    factory_mode: Literal["live", "dry-run"] = "dry-run"
    factory_config: str = "/opt/factory/.agent-factory/config.yaml"
    factory_home: str = "/opt/factory"  # templates/, prompts/, plugin/
    data_dir: str | None = None  # overrides config.factory.data_dir

    # Model access — exactly one is needed in live mode (see docs/adr/0005)
    claude_code_oauth_token: str | None = None  # from `claude setup-token` (subscription)
    anthropic_api_key: str | None = None
    claude_code_use_bedrock: str | None = None

    # Publishing
    github_token: str | None = None
    # Read-only token for importing templates/skills from private GitHub repos
    skills_github_token: str | None = None
    git_author_name: str = "Agent Factory"
    git_author_email: str = "agent-factory@localhost"

    cluster_name: str = "factory"
    dind_host: str = "dind"  # where kind's NodePorts are reachable from the engine
    app_host_port_base: int = 8081  # node_ports[i] -> host port base+i
    cors_origins: str = "http://localhost:8080,http://localhost:5173"

    def model_auth_configured(self) -> bool:
        return bool(self.claude_code_oauth_token or self.anthropic_api_key or self.claude_code_use_bedrock)
