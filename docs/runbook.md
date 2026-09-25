# Runbook

## First run (dry-run)
```bash
cp .env.example .env
make up
open http://localhost:8080
```
Submit an order and watch all 8 stations pass. The generated repo is real: see
`.factory-data/products/<slug>` (`git log`).

## First live run
1. Get a token: `claude setup-token` on your Mac. Put it in `.env` as
   `CLAUDE_CODE_OAUTH_TOKEN`.
2. Optional: set `GITHUB_TOKEN` (fine-grained, with Administration +
   Contents read/write on your account) so delivered products are pushed as
   private repos.
3. Set `FACTORY_MODE=live`, then `make up`, then `make logs`. Wait for
   `[bootstrap] cluster ready`.
4. The header pills should show `live`, `model ready` and `github on`.
5. Submit the order. A first iteration usually takes 20–60 minutes, depending
   on fix loops.

## Operating
| Situation | What to do |
|---|---|
| Run **held** | Read the Evidence panel. Fix the cause (config/template/prompt), or just click Resume to grant a fresh budget. |
| **Needs your answers** | Answer in the run panel; the run continues. |
| **Paused (usage limit)** | Nothing. It resumes when the window resets, or you can click Resume. |
| **Interrupted** after a restart | Click Resume. The run continues in its own worktree. |
| App not reachable on :8081 | `docker compose exec factory kubectl get pods -A` |
| Start over completely | `docker compose down -v && rm -rf .factory-data` |

## Troubleshooting
- **kind fails to create inside dind.** Give Docker Desktop at least 6 CPUs and
  12 GB of memory. On Rancher Desktop, use the dockerd (moby) engine.
  `make reset-cluster` recreates the cluster.
- **`docker info` fails in the factory container.** The dind certs may not be
  ready yet. Run `docker compose restart factory`.
- **trivy is slow the first time.** It downloads its vulnerability DB into the
  factory container on the first package step.
- **Agent sessions fail immediately.** Check the auth pill. Look in
  `docker compose logs factory` for the CLI's error.
