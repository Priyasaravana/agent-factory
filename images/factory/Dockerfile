# syntax=docker/dockerfile:1
# The factory runtime: engine + API, and every tool the stations shell out to.
# It never gets the host Docker socket; it talks to the dind sidecar over TLS.
FROM python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64

# Cluster tools (2026-11-24: the versions before these carried fixable CRITICAL CVEs in their Go
# builds; CI's `images` job fails on those now, S-08). Every download is checked against a checksum
# pinned here (S-07), never one fetched beside the binary at build time. To move a version, change
# it and run `scripts/pin-images.py --resolve --write`; CI's `images` job fails when a pinned sum
# isn't the one the release host publishes. Keep KIND_VERSION in step with ci.yml's cluster-security job.
ARG KIND_VERSION=v0.33.0
ARG KIND_SHA256_AMD64=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
ARG KIND_SHA256_ARM64=20022bee6cfcd5086cb7234d218e3454e6090022f2a8f55d1fa7fcf42c3867a2
# kubectl stays within one minor of the node image in cluster/kind-config.yaml (tests/test_cluster_tools.py)
ARG KUBECTL_VERSION=v1.37.2
ARG KUBECTL_SHA256_AMD64=c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4
ARG KUBECTL_SHA256_ARM64=fcc16f63186659d9722014b7e02d5fdd2d4e669cb8c0ab66365d7d3b8ba03509
ARG HELM_VERSION=v3.22.1
ARG HELM_SHA256_AMD64=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea
ARG HELM_SHA256_ARM64=4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d
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
 && case "$ARCH" in amd64) SUM=$KUBECTL_SHA256_AMD64;; arm64) SUM=$KUBECTL_SHA256_ARM64;; *) echo "no kubectl checksum for $ARCH"; exit 1;; esac \
 && test -n "$SUM" \
 && curl -fsSLo /tmp/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl \
 && echo "${SUM}  /tmp/kubectl" | sha256sum -c - \
 && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl && rm /tmp/kubectl && kubectl version --client
RUN ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && case "$ARCH" in amd64) SUM=$HELM_SHA256_AMD64;; arm64) SUM=$HELM_SHA256_ARM64;; *) echo "no helm checksum for $ARCH"; exit 1;; esac \
 && test -n "$SUM" \
 && cd /tmp && curl -fsSLO https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz \
 && echo "${SUM}  helm-${HELM_VERSION}-linux-${ARCH}.tar.gz" | sha256sum -c - \
 && tar -xzf helm-${HELM_VERSION}-linux-${ARCH}.tar.gz \
 && install -m 0755 linux-${ARCH}/helm /usr/local/bin/helm && rm -rf /tmp/helm-* /tmp/linux-${ARCH} && helm version --short
# No vulnerability-scanner binary here: the Package station runs the scanner as a
# container inside dind (see blueprints.*.scan_command in .agent-factory/config.yaml).

COPY --from=ghcr.io/astral-sh/uv:0.8@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 /uv /uvx /usr/local/bin/

# Non-root runtime user. HOME holds the Claude CLI state (~/.claude).
RUN useradd --create-home --uid 10001 factory && mkdir -p /data /opt/factory && chown factory /data
WORKDIR /opt/factory

COPY engine/pyproject.toml engine/uv.lock engine/
COPY engine/src engine/src
# Optional extras, e.g. --build-arg ENGINE_EXTRAS=aws for aws-sm:// secret references
ARG ENGINE_EXTRAS=""
# Exactly the lockfile's versions, each checked against its hash (S-07): the image ships what CI's
# pip-audit checked, not whatever resolves on the day. The project itself goes in last, without deps.
RUN cd engine \
 && uv export --frozen --no-dev --no-emit-project ${ENGINE_EXTRAS:+--extra ${ENGINE_EXTRAS}} -q -o /tmp/requirements.txt \
 && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
 && uv pip install --system --no-cache --no-deps . \
 && rm /tmp/requirements.txt && uv pip check --system

# The shipped default only: this install's config.local.yaml is kept out of the build (.dockerignore) and
# mounted read-only at run time (docker-compose.yml, FACTORY_CONFIG_LOCAL).
COPY .agent-factory .agent-factory
# Default skills (config: default_skills) at their pinned commits, so first start
# works offline. They are installed into the DB and managed on the Skills page.
# Build steps run as root and write into the image: tell the CLI not to drop to
# the runtime user here (it does so at run time, see cli.run).
RUN AGENT_FACTORY_ALLOW_ROOT=1 FACTORY_CONFIG=/opt/factory/.agent-factory/config.yaml agent-factory skills cache --out /opt/factory/skill-seeds
COPY prompts prompts
COPY workflow-templates workflow-templates
COPY packs packs
COPY plugin plugin
COPY cluster cluster
# Build context for the agent sandbox image; the engine builds it inside dind.
COPY images/sandbox images/sandbox

ENV FACTORY_HOME=/opt/factory \
    FACTORY_CONFIG=/opt/factory/.agent-factory/config.yaml \
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
