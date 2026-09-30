# syntax=docker/dockerfile:1
# Agent sandbox image (ADR-0014). Built by the engine INSIDE dind on first start
# (agent_factory.sandbox.SandboxManager), from a context it assembles:
#   Dockerfile (this file), claude (the CLI the engine's Agent SDK bundles),
#   plugin/ (the factory's own skills), egress.py (the allowlist proxy).
# It holds tools only: no credentials, no Docker CLI, no kubectl, no engine.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash git make curl jq ripgrep ca-certificates procps less \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/

# Interpreters for the generated apps, independent of this image's own Python:
# an app pins its version (.python-version) and uv picks it from here, offline
# (UV_PYTHON_DOWNLOADS=never at run time). Adding a version here is how the
# factory lets apps move to a newer Python; engine/tests/test_python_versions.py
# checks the golden path's pin is on this list.
ARG APP_PYTHONS="3.12 3.13 3.14"
ENV UV_PYTHON_INSTALL_DIR=/opt/python
# The interpreters are downloaded from GitHub releases: retry with backoff so a
# transient network or rate-limit error does not fail the whole image build.
RUN set -e; \
    for attempt in 1 2 3 4; do \
      if UV_HTTP_TIMEOUT=120 uv python install ${APP_PYTHONS}; then break; fi; \
      if [ "$attempt" = 4 ]; then echo "uv python install failed 4 times" >&2; exit 1; fi; \
      echo "uv python install failed (attempt $attempt/4), retrying" >&2; sleep $((attempt * 15)); \
    done; \
    chmod -R a+rX /opt/python && uv python list --only-installed

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
