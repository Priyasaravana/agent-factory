# ADR-0003: Everything in containers; kind runs inside Docker-in-Docker

**Status:** accepted · 2026-09-25

## Context
Agents run shell commands, build images and deploy to Kubernetes. None of this
should be able to affect the host laptop.

## Decision
- A `dind` sidecar (privileged, scoped to that container) hosts **all** images
  and the **kind** cluster. The factory talks to it over TLS (`dind:2376`) and
  never mounts the host Docker socket.
- kind's API server listens on `dind:6443`, with the certificate SAN set to
  `dind`. App NodePorts 30080–30099 map to dind ports 8081–8100 (5 before ADR-0015), which compose
  publishes to `127.0.0.1` on the host.
- The only host path mounted is `./.factory-data`. The factory process runs as
  an unprivileged user.
- Images are side-loaded with `kind load docker-image`, so there is no registry
  to run in v0.
- There is no ingress controller. NodePort services are enough for local use,
  and the community ingress-nginx project has been retired.

## Consequences
- `docker compose down -v` removes every trace, including the cluster.
- On a laptop, a few products can run at once (5 ports are mapped).
- Docker-in-Docker needs `privileged`. That is acceptable locally; the scaled
  design replaces it (see docs/scaling.md).
