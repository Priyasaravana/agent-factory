---
name: helm-kind-deploy
description: Use when a factory deployment to the local kind cluster fails and must be diagnosed and repaired.
---

# Helm on kind: diagnose and repair

The Deploy station ran `helm upgrade --install ... --wait` into namespace
`app-<slug>` with `image.repository`, `image.tag` and `service.nodePort` set.
Images are side-loaded with `kind load` — there is no registry.

## Diagnose (read-only first)
- `kubectl -n <ns> get pods,svc,pvc -o wide`
- `kubectl -n <ns> describe pod <pod>` — events: ImagePull, probes, OOM, scheduling
- `kubectl -n <ns> logs <pod> --all-containers --tail=100`

## Common causes on this golden path
| Symptom | Likely cause | Fix |
|---|---|---|
| ErrImageNeverPull / ImagePullBackOff | tag mismatch, pullPolicy | keep `pullPolicy: IfNotPresent`, don't rename `image.*` values |
| CrashLoopBackOff, import error | missing dependency in `pyproject.toml` | add dep, `uv lock` |
| Readiness never passes | `/readyz` failing: DB URL or Postgres not ready | check secret `database-url`, driver `postgresql+psycopg` |
| Read-only filesystem error | app writes to disk | write to `/tmp` (emptyDir) only |
| PVC Pending | storage class | kind ships `standard` (local-path); don't set storageClassName |

## Rules
- Fix the chart, Dockerfile or app config in the worktree. Never `kubectl edit`
  live objects or delete namespaces — the next deploy must be reproducible.
- Log the root cause with `log_decision`.
