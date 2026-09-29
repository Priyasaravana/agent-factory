# Upgrading the factory

## The routine
```bash
make upgrade      # = make backup, git pull --ff-only, rebuild, wait for health, make sandbox-check
```
Then open the UI: the header should show **readiness ready** and **sandbox ready**.
If anything looks wrong, roll back (below).

## What protects an upgrade

| Layer | Catches | Where |
|---|---|---|
| Unit and contract tests | engine, auth and UI logic; OpenAPI drift | CI `engine`, `auth`, `web` |
| Golden path | the app template still verifies; the chart lints | CI `golden-path` |
| **Images** | every image builds; engine tests pass **on the Python inside the factory image**; the sandbox image verifies the golden path as the sandbox user, without interpreter downloads | CI `images` |
| **Whole stack** | `docker compose up` in dry-run; sandbox isolation from inside a real sandbox; a browser walks sign-in → readiness → order → delivered → feedback → archive | CI `e2e` (screenshots and logs kept as evidence) |
| Next Python | engine tests on 3.13 / 3.14, advisory | CI `engine-next-python` |
| Version rules | factory image = auth image = CI Python; the sandbox offers the Python the golden path pins | `engine/tests/test_python_versions.py` |
| Weekly run | base-image and upstream drift, even with no PRs | CI schedule |

## Dependabot pull requests
- **Patch and minor updates with all CI jobs green:** merge.
- **Major updates, and anything under `images/` or `web/Dockerfile`:** merge only when `images` and `e2e` are green. Read the changelog for breaking changes.
- **Python base images** get patch updates only (Dependabot is told to ignore minor and major bumps). Moving the factory to a new Python is one deliberate PR: the three images and CI together.
- **Apps get newer Python independently.** Add the version to `APP_PYTHONS` in `images/sandbox/Dockerfile`, then bump `.python-version` and the runtime image in the template (or in an app).
- **Recommended in GitHub:** protect `main` with required status checks (`engine`, `auth`, `web`, `golden-path`, `images`, `e2e`). Then "auto-merge" on a Dependabot PR waits for them.

## Backup and rollback
```bash
make backup                                      # safe while running; → backups/agent-factory-<timestamp>/
make restore BACKUP=backups/agent-factory-<ts>   # stops the factory, keeps your current data aside
```
- **What a backup holds:**
  - a consistent SQLite snapshot of the factory database (orders, runs, events, workflows, skills);
  - the auth database (users, token hashes, audit);
  - product repos, hidden scenarios, run worktrees and artifacts.

  Not included: the kind cluster and images inside dind; they are rebuilt or re-delivered.
- **Rolling back code:** `git checkout <previous commit>`, then `make up`. If that version's data format differs, restore the backup taken before the upgrade. `backups/<…>/factory-version.txt` records which commit a backup came from.
- **Runs are safe across upgrades.** Each run is pinned to a workflow version and its pinned skills. Interrupted runs are marked on restart and resumed by a person.
