#!/usr/bin/env python3
"""Move the factory's tokens out of .env into mounted secret files, once (S-06). Python 3 standard library only.

    make secrets-files          (scripts/secrets-to-files.py --root .)

Each non-empty token below goes to `.secrets/<name>` (folder 0700, file 0600; gitignored, never in a build) and
its `.env` line becomes `<NAME>_FILE=/run/secrets/<name>`: compose mounts `.secrets/` read-only at /run/secrets
in the factory, and `env://<NAME>` reads the file when a step needs it, so the value is no longer in the engine's
environment. The old .env is kept as backups/.env.before-secrets-<time> (0600): check the factory, then delete it.
An existing file with a different value is never overwritten; nothing changes then.

On Linux the factory (uid 10001) must be able to read the files: `sudo chown -R 10001 .secrets` after this.
macOS (Docker Desktop) needs nothing more.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

NAMES = ("GITHUB_TOKEN", "SKILLS_GITHUB_TOKEN", "REPO_GITHUB_TOKEN", "SNYK_TOKEN")
_LINE = re.compile(r"^(?P<export>export\s+)?(?P<name>[A-Z0-9_]+)=(?P<value>.*)$")


def _value(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v.split(" #", 1)[0].strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    a = ap.parse_args(argv)
    env = a.root / ".env"
    if not env.is_file():
        print(f"no {env}: nothing to move")
        return 0
    lines = env.read_text().splitlines(keepends=True)
    secrets = a.root / ".secrets"
    moves: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = _LINE.match(line.rstrip("\n"))
        if m and m.group("name") in NAMES and (value := _value(m.group("value"))):
            target = secrets / m.group("name").lower()
            if target.exists() and target.read_text().strip() != value:
                print(f"refused: {target} already exists with another value; nothing was changed")
                return 1
            moves.append((i, m.group("name"), value))
    if not moves:
        print("nothing to move: no token is set in .env")
        return 0
    old_umask = os.umask(0o077)
    try:
        secrets.mkdir(mode=0o700, exist_ok=True)
        secrets.chmod(0o700)
        backups = a.root / "backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        keep = backups / f".env.before-secrets-{time.strftime('%Y%m%d-%H%M%S')}"
        keep.write_text("".join(lines))
        keep.chmod(0o600)
        for i, name, value in moves:
            f = secrets / name.lower()
            f.write_text(value + "\n")
            f.chmod(0o600)
            lines[i] = f"{name}_FILE=/run/secrets/{name.lower()}\n"
        tmp = env.with_name(".env.tmp")
        tmp.write_text("".join(lines))
        tmp.chmod(0o600)
        tmp.replace(env)
    finally:
        os.umask(old_umask)
    print(f"moved {', '.join(n for _, n, _ in moves)} to {secrets}/ (0600); .env now names their files")
    print(f"the old .env is {keep} (0600): delete it once the factory works (make upgrade)")
    if sys.platform.startswith("linux"):
        print("Linux: let the factory read them:  sudo chown -R 10001 .secrets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
