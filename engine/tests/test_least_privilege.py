"""Least privilege of the shipped stack (ADR-0018), checked on the files we ship.

The running stack is checked by scripts/privilege-check.sh (CI e2e); these tests
catch a regression before any image is built."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
RUNTIME_UID = "10001"


def test_only_dind_is_privileged():
    assert [name for name, svc in COMPOSE.items() if svc.get("privileged")] == ["dind"]


def test_long_running_services_drop_everything():
    for name in ("factory", "auth", "web"):
        svc = COMPOSE[name]
        assert svc.get("cap_drop") == ["ALL"], f"{name}: cap_drop [ALL]"
        assert not svc.get("cap_add"), f"{name}: no capabilities added back"
        assert "no-new-privileges:true" in svc.get("security_opt", []), f"{name}: no-new-privileges"


def test_the_factory_never_runs_as_root():
    assert COMPOSE["factory"]["user"] == f"{RUNTIME_UID}:{RUNTIME_UID}"
    dockerfile = (ROOT / "images" / "factory" / "Dockerfile").read_text()
    users = re.findall(r"^USER\s+(\S+)", dockerfile, re.M)
    assert users and users[-1] == f"{RUNTIME_UID}:{RUNTIME_UID}", "the image's final USER is the runtime user"
    entrypoint = (ROOT / "cluster" / "entrypoint.sh").read_text()
    assert "setpriv" not in entrypoint and "chown" not in entrypoint, "no root phase in the entrypoint"
    assert "refusing to run as root" in entrypoint


def test_the_root_step_is_one_shot_offline_and_minimal():
    init = COMPOSE["factory-init"]
    assert init["user"] == "0:0" and init["restart"] == "no"
    assert init["network_mode"] == "none"
    assert init["cap_drop"] == ["ALL"] and sorted(init["cap_add"]) == ["CHOWN", "DAC_OVERRIDE", "FOWNER"]
    assert "no-new-privileges:true" in init["security_opt"]
    assert COMPOSE["factory"]["depends_on"]["factory-init"]["condition"] == "service_completed_successfully"
    script = (ROOT / "cluster" / "init.sh").read_text()
    assert f"RUNTIME_UID={RUNTIME_UID}" in script and "/factory-certs" in script


def test_the_factory_gets_certs_from_init_not_from_dind():
    mounts = COMPOSE["factory"]["volumes"]
    assert "factory-certs:/factory-certs:ro" in mounts
    assert not any(m.startswith("dind-certs:") for m in mounts), "dind's cert volume is only read by factory-init"
    assert "DOCKER_CERT_PATH=/factory-certs" in (ROOT / "images" / "factory" / "Dockerfile").read_text()


def test_auth_and_web_images_are_non_root():
    assert re.search(r"^USER\s+auth\b", (ROOT / "images" / "auth" / "Dockerfile").read_text(), re.M)
    web = (ROOT / "web" / "Dockerfile").read_text()
    assert "nginx-unprivileged" in web, "unprivileged nginx (uid 101)"
    assert "listen 8080;" in (ROOT / "web" / "nginx.conf").read_text()
    assert "127.0.0.1:8080:8080" in COMPOSE["web"]["ports"]
