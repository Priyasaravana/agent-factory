"""scripts/backup.sh and scripts/restore.sh (S-17): encrypted backups, and a restore that works on a
fresh recovery checkout. Docker is a stub (it records its calls); `age` is the real one when installed,
else a stub that passes data through, so the script's own steps are what's tested."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


PINNED = '{"services": {"dind": {"image": "docker:27-dind@sha256:' + "a" * 64 + '"}}}'
UNPINNED = '{"services": {"dind": {"image": "docker:27-dind"}}}'


def _checkout(tmp_path: Path, compose_config: str = PINNED) -> tuple[Path, dict[str, str]]:
    """A fresh checkout: the scripts and nothing else (no backups/, no .factory-data), with stub tools.
    The docker stub answers `compose config` with `compose_config`, for the image check (S-07)."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    for name in ("backup.sh", "restore.sh", "pin-images.py"):
        shutil.copy(REPO / "scripts" / name, root / "scripts" / name)
    (tmp_path / "compose-config.json").write_text(compose_config)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    # records each call; `compose run ... < auth.db` keeps the auth.db it was given
    docker.write_text(
        '#!/usr/bin/env bash\necho "$@" >> "$DOCKER_LOG"\nif [ "$2" = run ]; then cat > "$DOCKER_LOG.stdin"; fi\n'
        'case "$*" in *"config --profiles"*) ;; *"config --format json"*) cat "$COMPOSE_CONFIG";; esac\n'
    )
    docker.chmod(0o755)
    if shutil.which("age") is None:
        age = bin_dir / "age"  # -d -i KEY FILE: pass the file through (the stub "encrypts" nothing)
        age.write_text('#!/usr/bin/env bash\nif [ "$1" = -d ]; then cat "$4"; else cat; fi\n')
        age.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "COMPOSE_CONFIG": str(tmp_path / "compose-config.json"),
    }
    return root, env


def _encrypted_backup(tmp_path: Path) -> tuple[Path, Path]:
    """A backup as backup.sh writes it, kept outside the checkout (as after a disk loss)."""
    content = tmp_path / "content"
    (content / "data").mkdir(parents=True)
    (content / "data" / "products.txt").write_text("bookmarks\n")
    (content / "factory.db").write_text("factory-db")
    (content / "auth.db").write_text("auth-db")
    with tarfile.open(content / "factory-data.tgz", "w:gz") as t:
        t.add(content / "data" / "products.txt", arcname="products.txt")
    shutil.rmtree(content / "data")
    key = tmp_path / "backup.key"
    tar_path = tmp_path / "plain.tar"
    with tarfile.open(tar_path, "w") as t:
        for f in content.iterdir():
            t.add(f, arcname=f.name)
    out = tmp_path / "offsite" / "agent-factory-20261119-120000.tar.age"
    out.parent.mkdir()
    if shutil.which("age"):
        subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
        recipient = subprocess.run(["age-keygen", "-y", str(key)], check=True, capture_output=True, text=True)
        subprocess.run(
            ["age", "-r", recipient.stdout.strip(), "-o", str(out), str(tar_path)], check=True, capture_output=True
        )
    else:
        key.write_text("stub")
        shutil.copy(tar_path, out)
    return out, key


def test_an_encrypted_backup_restores_on_a_fresh_checkout(tmp_path):
    """PR #186 re-review: restore made its temporary folder under backups/, which a fresh recovery
    checkout doesn't have, so restoring an encrypted backup from elsewhere failed before decrypting."""
    root, env = _checkout(tmp_path)
    backup, key = _encrypted_backup(tmp_path)
    assert not (root / "backups").exists()
    r = subprocess.run(
        ["bash", "scripts/restore.sh", str(backup)],
        cwd=root,
        env={**env, "BACKUP_AGE_IDENTITY": str(key)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert (root / ".factory-data" / "factory.db").read_text() == "factory-db"
    assert (root / ".factory-data" / "products.txt").read_text() == "bookmarks\n"
    assert Path(env["DOCKER_LOG"] + ".stdin").read_text() == "auth-db", "auth.db went in on stdin"
    assert oct((root / "backups").stat().st_mode)[-3:] == "700"
    assert list((root / "backups").iterdir()) == [], "the decrypted copy is removed"
    calls = Path(env["DOCKER_LOG"]).read_text().splitlines()
    assert calls.index("compose config --format json") < calls.index("compose stop factory auth web"), "checked first"


def test_a_restore_never_starts_an_image_that_is_not_pinned(tmp_path):
    """S-07 review on #204: a restore starts the stack too, so it refuses what `make up` refuses."""
    root, env = _checkout(tmp_path, UNPINNED)
    backup, key = _encrypted_backup(tmp_path)
    r = subprocess.run(
        ["bash", "scripts/restore.sh", str(backup)],
        cwd=root,
        env={**env, "BACKUP_AGE_IDENTITY": str(key)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode != 0 and "not pinned by digest" in r.stdout + r.stderr
    calls = Path(env["DOCKER_LOG"]).read_text().splitlines()
    assert not any(c.startswith(("compose stop", "compose up", "compose run")) for c in calls), calls
    assert not (root / ".factory-data").exists(), "refused before anything was stopped or moved"


def test_an_encrypted_backup_needs_its_key(tmp_path):
    root, env = _checkout(tmp_path)
    backup, _ = _encrypted_backup(tmp_path)
    r = subprocess.run(["bash", "scripts/restore.sh", str(backup)], cwd=root, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "BACKUP_AGE_IDENTITY" in r.stdout + r.stderr
    assert not (root / ".factory-data").exists(), "nothing was touched"


def test_a_backup_is_not_written_in_clear_unless_asked(tmp_path):
    root, env = _checkout(tmp_path)
    r = subprocess.run(["bash", "scripts/backup.sh"], cwd=root, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "BACKUP_PLAINTEXT=1" in r.stderr
    assert not (root / "backups").exists() and not Path(env["DOCKER_LOG"]).exists(), "nothing was read"
