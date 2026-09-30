"""The factory's data must stay owned by the runtime user (uid 10001).

`docker compose exec` runs as root by default. A CLI call as root once left the
sandbox probe dir root-owned, and the sandbox (uid 10001) could not write it
(CI e2e: "worktree is writable ... Permission denied")."""

from __future__ import annotations

import pwd
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_factory import cli
from agent_factory.sandbox import manager
from agent_factory.sandbox.spec import SANDBOX_UID

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def as_root(monkeypatch):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv("AGENT_FACTORY_ALLOW_ROOT", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["agent-factory", "sandbox", "check"])


def test_cli_as_root_re_execs_as_the_factory_user(as_root, monkeypatch):
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=10001, pw_gid=10001, pw_dir="/home/factory"))
    assert cli.drop_root() == [
        "/usr/bin/setpriv",
        "--reuid=10001",
        "--regid=10001",
        "--init-groups",
        "agent-factory",
        "sandbox",
        "check",
    ]


def test_cli_stays_put_when_not_root_outside_the_image_or_opted_out(as_root, monkeypatch):
    def no_user(_):
        raise KeyError

    monkeypatch.setattr(pwd, "getpwnam", no_user)
    assert cli.drop_root() is None, "no factory user: not inside the image"
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=10001, pw_gid=10001, pw_dir="/h"))
    monkeypatch.setenv("AGENT_FACTORY_ALLOW_ROOT", "1")
    assert cli.drop_root() is None
    monkeypatch.delenv("AGENT_FACTORY_ALLOW_ROOT")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 10001)
    assert cli.drop_root() is None


def test_sandbox_dirs_created_as_root_are_handed_to_the_sandbox_user(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(manager.os, "chown", lambda p, u, g: calls.append((p, u, g)))
    monkeypatch.setattr(manager.os, "geteuid", lambda: 0)
    manager.hand_to_sandbox(tmp_path)
    assert calls == [(tmp_path, SANDBOX_UID, SANDBOX_UID)]
    calls.clear()
    monkeypatch.setattr(manager.os, "geteuid", lambda: SANDBOX_UID)
    manager.hand_to_sandbox(tmp_path)
    assert calls == []


def test_every_exec_into_the_factory_container_uses_the_runtime_user():
    files = [ROOT / "Makefile", *(ROOT / ".github" / "workflows").glob("*.yml"), *(ROOT / "scripts").glob("*.sh")]
    files += [*(ROOT / "docs").glob("*.md"), ROOT / "README.md"]
    exec_factory = re.compile(r"docker compose exec\b((?:\s+-\S+(?:\s+(?!factory\b)\S+)?)*)\s+factory\b")
    # privilege-check.sh omits -u on purpose: it proves the image's default user is not root
    files = [f for f in files if f.name != "privilege-check.sh"]
    bad = []
    for f in files:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            for m in exec_factory.finditer(line):
                if "-u factory" not in m.group(0):
                    bad.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not bad, "use `docker compose exec -u factory factory …`:\n" + "\n".join(bad)


def test_the_console_script_drops_root_and_main_does_not(as_root, monkeypatch):
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=10001, pw_gid=10001, pw_dir="/home/factory"))
    execs, mains = [], []
    monkeypatch.setattr(cli.os, "execve", lambda *a: execs.append(a))
    monkeypatch.setattr(cli, "main", lambda: mains.append(1))
    cli.run()
    assert execs and execs[0][0] == "/usr/bin/setpriv" and execs[0][2]["HOME"] == "/home/factory"
    text = (ROOT / "engine" / "pyproject.toml").read_text()
    assert 'agent-factory = "agent_factory.cli:run"' in text


def test_image_build_steps_that_call_the_cli_keep_root():
    """Build steps write into root-owned image paths; the runtime drop must not apply
    (CI images: "Permission denied: '/opt/factory/skill-seeds'")."""
    bad = []
    for f in ROOT.glob("**/Dockerfile*"):
        if "node_modules" in f.parts:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith("RUN") and re.search(r"\bagent-factory\b", line):
                if "AGENT_FACTORY_ALLOW_ROOT=1" not in line:
                    bad.append(f"{f.relative_to(ROOT)}:{n}")
    assert not bad, "set AGENT_FACTORY_ALLOW_ROOT=1 on build steps that run the CLI: " + ", ".join(bad)
