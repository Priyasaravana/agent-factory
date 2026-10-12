"""Fresh start (ADR-0037 §7): the one-lane workflows replace the per-blueprint templates with no
migration code. Data from before is dropped on purpose, by a person, with one command:

    agent-factory reset --confirm

It deletes every product, change, event, evaluation and workflow version, and the files that
belong to them (product repos, worktrees, holdout scenarios, evidence). Members, imported
skills and the config stay. The next start seeds the standard lane again.

The engine refuses to start on data from an older generation (`check`), naming the command,
rather than run new workflows against old products.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_factory.state.base import StateStore

GENERATION = "3"  # 2: one standard lane with windows; 3: one workflow for the org (ADR-0037)
_KEY = "generation"
DATA_FOLDERS = ("products", "runs", "holdout", "artifacts")


class ResetRequired(RuntimeError):
    """Stored data is from before the one-lane workflows."""


def check(store: StateStore) -> None:
    """Stamp a new database; refuse data from an older generation."""
    if store.get_meta(_KEY) == GENERATION:
        return
    work = {t: n for t, n in store.count_work().items() if n}
    if not work:
        store.set_meta(_KEY, GENERATION)
        return
    held = ", ".join(f"{n} {t}" for t, n in work.items())
    raise ResetRequired(
        "this data is from before the one-lane workflows (ADR-0037) and is not migrated: "
        f"{held}. Run `agent-factory reset --confirm` to drop it and start fresh "
        "(members, skills and the config are kept)."
    )


def plan(store: StateStore, data_dir: Path) -> dict[str, int]:
    """What a reset would delete: rows per table and files per data folder."""
    rows = store.count_work()
    files = {
        f"{d}/": sum(1 for p in (data_dir / d).rglob("*") if p.is_file())
        for d in DATA_FOLDERS
        if (data_dir / d).is_dir()
    }
    return {**rows, **files}


def reset(store: StateStore, data_dir: Path) -> dict[str, int]:
    """Drop the work and its files, and stamp the database as the current generation."""
    done = plan(store, data_dir)
    store.reset_work()
    for d in DATA_FOLDERS:
        shutil.rmtree(data_dir / d, ignore_errors=True)
    store.set_meta(_KEY, GENERATION)
    return done
