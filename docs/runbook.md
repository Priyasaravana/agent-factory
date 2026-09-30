# Runbook

## First run (dry-run)
```bash
cp .env.example .env
make up                      # prints the first sign-in if a one-time password was generated
open http://localhost:8080
```
Sign in as `admin`. Either set `FACTORY_ADMIN_PASSWORD` in `.env` before the
first start, or use the one-time password `make up` prints
(`docker compose exec auth cat /data/initial-admin-password`). You'll then be
asked to choose your own. Submit an order and watch all 8 stations pass. The generated repo is real: see
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
| App not reachable on :8081 | `docker compose exec -u factory factory kubectl get pods -A` |
| **Locked out** / forgot the admin password | `make reset-admin` prints a one-time password (must be changed at sign-in). |
| Add a person | Admin → Add user (member or admin) with a temporary password; they choose their own at first sign-in. |
| Script or CI access | Account → API tokens; send `Authorization: Bearer <token>` to `http://localhost:8080/api/...` |
| **Sandbox preparing / failed** (header pill) | First start builds the sandbox image inside dind (a few minutes); runs wait for it. If it failed, the pill's tooltip and `docker compose logs factory` say why. Then `make sandbox-check`. |
| An agent needs a site that is blocked | Add `host:443` to `sandbox.egress` in `.agent-factory/config.yaml` and restart the factory. Denied attempts: `docker compose exec dind docker logs factory-egress \| grep deny` |
| Run **failed at readiness** | The repo lost an agent-readiness Level 3 signal (see the scorecard event). The build agent gets the missing list and fixes it; a secret found by the scan must also be rotated. |
| **Order refused: "preflight failed"** | The message lists the failing checks. Integrations shows every check with its reasons. Fix the cause, then **Check now**. Nothing was started and no model usage was spent. |
| **No free app port** | Open an order you no longer need → **Archive**. The app is removed from the cluster and its port freed; its repo and history are kept. |
| Want all 20 app ports (installs from before ADR-0015) | `make reset-cluster`, then re-deliver apps you still need (new order or feedback). Until then the readiness page shows how many ports the cluster maps. |
| **Upgrade the factory** | `make upgrade` (backup → pull → rebuild → check). Details and rollback: [upgrading.md](upgrading.md). |
| **Is the factory delivering? Who is it waiting on?** | **Outcomes** page (or `GET /api/outcomes?days=30`). Definitions: [outcomes.md](outcomes.md). The "Waiting on a person now" board lists every run that needs someone, and who. |
| Check least privilege | `make privilege-check`: factory, auth and web are non-root with no capabilities; only dind is privileged (ADR-0018). |
| Back up / restore | `make backup` (safe while running) · `make restore BACKUP=backups/agent-factory-<ts>` |
| Start over completely | `docker compose down -v && rm -rf .factory-data` |

## Troubleshooting
- **kind fails to create inside dind.** Give Docker Desktop at least 6 CPUs and
  12 GB of memory. On Rancher Desktop, use the dockerd (moby) engine.
  `make reset-cluster` recreates the cluster.
- **`docker info` fails in the factory container.** The factory reads dind's
  client certs from the `factory-certs` volume, which factory-init fills. Run
  `docker compose up -d`: it reruns factory-init and then the factory.
  `docker compose logs factory-init` shows what it copied.
- **"refusing to run as root".** The factory image runs as uid 10001 (ADR-0018).
  Start it with `docker compose up`, not `docker run --user root`.
- **Permission denied under `/data`.** Something wrote files as another user.
  `docker compose up -d` reruns factory-init, which hands `/data` back to uid
  10001. `make privilege-check` shows each container's user and capabilities.
- **The scan step is slow the first time.** The Package station runs the scanner
  (`scan_command` in `config.yaml`) as a container inside dind. The first run
  pulls the image and downloads its vulnerability DB into the `trivy-cache`
  volume. Pin the image to a digest you have verified.
- **Image build fails downloading a tool.** Each tool has its own `RUN` line in
  `images/factory/Dockerfile`, so the failing layer names the tool. Bump that
  tool's `ARG` version.
- **Agent sessions fail immediately.** Check the auth and sandbox pills. Look in
  `docker compose logs factory` for the CLI's error.
- **Upgrading from before the sandbox.** `make up` recreates dind with the
  `.factory-data` mount it now needs. Existing workflows keep their stations:
  add the Readiness check in Workflows → Edit (drag it after verify), or reset
  the lane from the `default` template, and publish.
