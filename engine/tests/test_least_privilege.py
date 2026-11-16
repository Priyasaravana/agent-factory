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


def test_the_model_gateway_is_optional_unprivileged_and_isolated():
    """ADR-0036: off unless the `gateway` profile is on; no capabilities, no published port,
    and never the factory's .env (it gets only its own key and the provider key)."""
    for name in ("model-gateway", "model-gateway-db"):
        svc = COMPOSE[name]
        assert svc.get("profiles") == ["gateway"], f"{name}: only with COMPOSE_PROFILES=gateway"
        assert svc.get("cap_drop") == ["ALL"] and not svc.get("cap_add"), f"{name}: cap_drop [ALL]"
        assert "no-new-privileges:true" in svc.get("security_opt", []), f"{name}: no-new-privileges"
        assert not svc.get("ports"), f"{name}: reached only inside the stack"
        assert "env_file" not in svc, f"{name}: never the factory's .env (GitHub tokens …)"
    assert COMPOSE["model-gateway-db"]["user"] == "70:70"
    assert "non_root" in COMPOSE["model-gateway"]["image"]
    assert set(COMPOSE["model-gateway"]["environment"]) == {"LITELLM_MASTER_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL"}


def test_the_gateway_proof_reaches_only_the_engine_and_nginx_each_with_its_own_copy():
    """S-01 (assessment 2026-11-15): identity headers count only with the proof nginx adds."""
    factory, web, init = COMPOSE["factory"], COMPOSE["web"], COMPOSE["factory-init"]
    assert factory["environment"]["AUTH_MODE"] == "gateway"
    assert factory["environment"]["GATEWAY_PROOF_FILE"] == "/run/gateway-proof/secret"
    assert "gateway-proof-engine:/run/gateway-proof:ro" in factory["volumes"]
    assert web["volumes"] == ["gateway-proof-web:/etc/nginx/gateway-proof:ro"], "nginx gets its config line only"
    assert web["depends_on"]["factory-init"]["condition"] == "service_completed_successfully"
    assert not any("gateway-proof-web" in v for v in factory["volumes"])
    holders = [name for name, svc in COMPOSE.items() if any("gateway-proof" in str(v) for v in svc.get("volumes", []))]
    assert sorted(holders) == ["factory", "factory-init", "web"], "no other service holds the proof"
    init_mounts = {"gateway-proof-engine:/gateway-proof/engine", "gateway-proof-web:/gateway-proof/web"}
    assert init_mounts <= set(init["volumes"])
    assert "ports" not in factory, "the engine publishes no port: the gateway is the only way in"
    script = (ROOT / "cluster" / "init.sh").read_text()
    assert "/dev/urandom" in script and "chmod 0400" in script and "umask 077" in script

    conf = (ROOT / "web" / "nginx.conf").read_text()
    assert "include /etc/nginx/gateway-proof/proof.conf;" in conf
    to_engine = [block for block in re.split(r"\n  location ", conf) if "proxy_pass $engine" in block]
    assert len(to_engine) == 2, "the API and the webhook"
    for block in to_engine:
        assert "proxy_set_header X-Gateway-Proof $gateway_proof;" in block, block.splitlines()[0]
    to_auth = [block for block in re.split(r"\n  location ", conf) if "proxy_pass $auth" in block]
    assert to_auth and not any("gateway_proof" in block for block in to_auth), "the auth service never gets it"
