# syntax=docker/dockerfile:1
# Agent sandbox image (ADR-0014). Built by the engine INSIDE dind on first start
# (agent_factory.sandbox.SandboxManager), from a context it assembles:
#   Dockerfile (this file), claude (the CLI the engine's Agent SDK bundles),
#   plugin/ (the factory's own skills), egress.py (the allowlist proxy).
# It holds tools only: no credentials, no Docker CLI, no kubectl, no engine.
FROM python:3.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash git make curl jq ripgrep ca-certificates procps less \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/

COPY claude /usr/local/bin/claude
COPY plugin /opt/factory/plugin
COPY egress.py /opt/egress.py
RUN chmod 0755 /usr/local/bin/claude && chmod -R a+rX /opt/factory /opt/egress.py \
    && useradd --uid 10001 --home-dir /home/agent --no-create-home agent \
    && git config --system --add safe.directory '*' \
    && git config --system init.defaultBranch main

USER 10001
ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:$PATH \
    UV_PYTHON_DOWNLOADS=never \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
WORKDIR /home/agent
CMD ["claude", "--version"]
