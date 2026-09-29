---
name: helm-kind-deploy
description: Use when a factory deployment to the local kind cluster fails and must be diagnosed and repaired.
---

# Helm on kind: diagnose and repair

The Deploy station ran `helm upgrade --install ... --wait` into namespace
`app-<slug>` with `image.repository`, `image.tag` and `service.nodePort` set.
Images are side-loaded with `kind load` — there is no registry.

## Diagnose from the evidence
You run in a sandbox without cluster access. The engine already collected, in
the evidence you were given:
- `kubectl -n <ns> get pods -o wide` and recent namespace events
- `kubectl -n <ns> describe pods` — ImagePull, probes, OOM, scheduling
- `kubectl -n <ns> logs … --all-containers --tail=80`
Reproduce locally what you can: `make verify`, `uv run uvicorn app.main:app` and
curl it, render the chart in your head against `values.yaml`.

## Common causes on this golden path
| Symptom | Likely cause | Fix |
|---|---|---|
| ErrImageNeverPull / ImagePullBackOff | tag mismatch, pullPolicy | keep `pullPolicy: IfNotPresent`, don't rename `image.*` values |
| CrashLoopBackOff, import error | missing dependency in `pyproject.toml` | add dep, `uv lock` |
| Readiness never passes | `/readyz` failing: DB URL or Postgres not ready | check secret `database-url`, driver `postgresql+psycopg` |
| Read-only filesystem error | app writes to disk | write to `/tmp` (emptyDir) only |
| PVC Pending | storage class | kind ships `standard` (local-path); don't set storageClassName |

## Rules
- Fix the chart, Dockerfile or app config in the worktree; the engine redeploys.
  The fix must live in the repo — the next deploy must be reproducible.
- Log the root cause with `log_decision`.
