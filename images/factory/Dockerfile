# syntax=docker/dockerfile:1
# The factory runtime: engine + API, and every tool the stations shell out to.
# It never gets the host Docker socket; it talks to the dind sidecar over TLS.
FROM python:3.12-slim-bookworm

ARG KIND_VERSION=v0.27.0
ARG KUBECTL_VERSION=v1.32.3
ARG HELM_VERSION=v3.17.2
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
      git make curl ca-certificates jq gnupg openssh-client \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
         > /etc/apt/sources.list.d/docker.list \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/gh.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/gh.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/gh.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin gh \
    && rm -rf /var/lib/apt/lists/*

# One tool per layer so a failed download names itself in the build log.
RUN ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && curl -fsSLo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${ARCH} \
 && chmod +x /usr/local/bin/kind && kind version
RUN ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl \
 && chmod +x /usr/local/bin/kubectl && kubectl version --client
RUN ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && curl -fsSL https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz | tar -xz -C /tmp \
 && mv /tmp/linux-${ARCH}/helm /usr/local/bin/helm && helm version --short
# No vulnerability-scanner binary here: the Package station runs the scanner as a
# container inside dind (see product_lines.*.scan_command in .agent-factory/config.yaml).

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/

# Non-root runtime user. HOME holds the Claude CLI state (~/.claude).
RUN useradd --create-home --uid 10001 factory && mkdir -p /data /opt/factory && chown factory /data
WORKDIR /opt/factory

COPY engine/pyproject.toml engine/
COPY engine/src engine/src
RUN uv pip install --system --no-cache ./engine

COPY .agent-factory .agent-factory
COPY prompts prompts
COPY plugin plugin
COPY templates templates
COPY cluster cluster

ENV FACTORY_HOME=/opt/factory \
    FACTORY_CONFIG=/opt/factory/.agent-factory/config.yaml \
    DATA_DIR=/data \
    KUBECONFIG=/data/kube/config \
    DOCKER_CERT_PATH=/home/factory/.docker/certs \
    PYTHONUNBUFFERED=1

# Starts as root only to copy the dind client certs and fix volume ownership,
# then the entrypoint drops to the unprivileged `factory` user (setpriv).
EXPOSE 8000
ENTRYPOINT ["/opt/factory/cluster/entrypoint.sh"]
CMD ["agent-factory", "serve"]
