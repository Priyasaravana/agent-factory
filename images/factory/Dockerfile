# syntax=docker/dockerfile:1
# The factory runtime: engine + API, and every tool the stations shell out to.
# It never gets the host Docker socket; it talks to the dind sidecar over TLS.
FROM python:3.12-slim-bookworm

# Cluster tools (2026-11-24: the versions before these carried fixable CRITICAL CVEs in their Go
# builds; CI's `images` job fails on those now, S-08). Every download is checked before use: kind
# against the checksums pinned here, kubectl and helm against the checksum file their release host
# publishes beside the binary (pinning those two here too is S-07). Keep KIND_VERSION in step with
# ci.yml's cluster-security job.
ARG KIND_VERSION=v0.33.0
ARG KIND_SHA256_AMD64=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
ARG KIND_SHA256_ARM64=20022bee6cfcd5086cb7234d218e3454e6090022f2a8f55d1fa7fcf42c3867a2
# kubectl stays within one minor of the node image in cluster/kind-config.yaml (tests/test_cluster_tools.py)
ARG KUBECTL_VERSION=v1.37.2
ARG HELM_VERSION=v3.22.1
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
 && case "$ARCH" in amd64) SUM=$KIND_SHA256_AMD64;; arm64) SUM=$KIND_SHA256_ARM64;; *) echo "no kind checksum for $ARCH"; exit 1;; esac \
 && curl -fsSLo /tmp/kind https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${ARCH} \
 && echo "${SUM}  /tmp/kind" | sha256sum -c - \
 && install -m 0755 /tmp/kind /usr/local/bin/kind && rm /tmp/kind && kind version
RUN ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && curl -fsSLo /tmp/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl \
 && echo "$(curl -fsSL https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl.sha256)  /tmp/kubectl" | sha256sum -c - \
 && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl && rm /tmp/kubectl && kubectl version --client
RUN ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && cd /tmp && curl -fsSLO https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz \
 && curl -fsSL https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz.sha256sum | sha256sum -c - \
 && tar -xzf helm-${HELM_VERSION}-linux-${ARCH}.tar.gz \
 && install -m 0755 linux-${ARCH}/helm /usr/local/bin/helm && rm -rf /tmp/helm-* /tmp/linux-${ARCH} && helm version --short
# No vulnerability-scanner binary here: the Package station runs the scanner as a
# container inside dind (see blueprints.*.scan_command in .agent-factory/config.yaml).

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/

# Non-root runtime user. HOME holds the Claude CLI state (~/.claude).
RUN useradd --create-home --uid 10001 factory && mkdir -p /data /opt/factory && chown factory /data
WORKDIR /opt/factory

COPY engine/pyproject.toml engine/
COPY engine/src engine/src
# Optional extras, e.g. --build-arg ENGINE_EXTRAS=aws for aws-sm:// secret references
ARG ENGINE_EXTRAS=""
RUN uv pip install --system --no-cache "./engine${ENGINE_EXTRAS:+[${ENGINE_EXTRAS}]}"

COPY .agent-factory .agent-factory
# Default skills (config: default_skills) at their pinned commits, so first start
# works offline. They are installed into the DB and managed on the Skills page.
# Build steps run as root and write into the image: tell the CLI not to drop to
# the runtime user here (it does so at run time, see cli.run).
RUN AGENT_FACTORY_ALLOW_ROOT=1 FACTORY_CONFIG=/opt/factory/.agent-factory/config.yaml \
    FACTORY_CONFIG_LOCAL=/opt/factory/.agent-factory/config.local.yaml agent-factory skills cache --out /opt/factory/skill-seeds
COPY prompts prompts
COPY workflow-templates workflow-templates
COPY packs packs
COPY plugin plugin
COPY cluster cluster
# Build context for the agent sandbox image; the engine builds it inside dind.
COPY images/sandbox images/sandbox

ENV FACTORY_HOME=/opt/factory \
    FACTORY_CONFIG=/opt/factory/.agent-factory/config.yaml \
    FACTORY_CONFIG_LOCAL=/opt/factory/.agent-factory/config.local.yaml \
    DATA_DIR=/data \
    KUBECONFIG=/data/kube/config \
    DOCKER_CERT_PATH=/factory-certs \
    PYTHONUNBUFFERED=1

# Runs as the unprivileged factory user; nothing in this container is root
# (ADR-0018). Certs and /data ownership are prepared by the factory-init service.
USER 10001:10001
ENV HOME=/home/factory USER=factory
EXPOSE 8000
ENTRYPOINT ["/opt/factory/cluster/entrypoint.sh"]
CMD ["agent-factory", "serve"]
