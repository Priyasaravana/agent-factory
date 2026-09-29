"""Isolation checks, run INSIDE a sandbox by `agent-factory sandbox check`.
Standard library only; prints one JSON list. __PROXY__ is filled in by the engine."""

import json
import os
import pathlib
import socket

out: list[dict[str, object]] = []
FORBIDDEN = ("GITHUB", "GH_", "AWS_", "DOCKER", "KUBE", "SKILLS_", "SECRET", "PASSWORD")


def check(name: str, ok: object, detail: object = "") -> None:
    out.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})


def conn(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=4).close()
        return True
    except OSError:
        return False


def via_proxy(target: str) -> str:
    host, port = "__PROXY__".split(":")
    s = socket.create_connection((host, int(port)), timeout=8)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    line = s.recv(200).decode(errors="replace").split("\r\n")[0]
    s.close()
    return line


bad = [k for k in os.environ if any(s in k.upper() for s in FORBIDDEN)]
check("no factory secrets in env", not bad, bad)
docker_bits = ["/var/run/docker.sock", "/certs", "/home/factory/.docker"]
check("no Docker socket or TLS keys", not any(pathlib.Path(p).exists() for p in docker_bits))
check("no kubeconfig", not pathlib.Path("/data/kube/config").exists())
check("holdout scenarios not mounted", not pathlib.Path("/data/holdout").exists())
check("factory database not mounted", not pathlib.Path("/data/factory.db").exists())
check("no direct internet (1.1.1.1:443)", not conn("1.1.1.1", 443))
check("docker daemon unreachable (docker:2376)", not conn("docker", 2376))
check("factory engine unreachable (factory:8000)", not conn("factory", 8000))
try:
    check("egress denies github.com:443", " 403 " in via_proxy("github.com:443"))
    check("egress allows api.anthropic.com:443", " 200 " in via_proxy("api.anthropic.com:443"))
except OSError as e:
    check("egress proxy reachable", False, e)
try:
    pathlib.Path("/opt/factory/plugin/x").write_text("x")
    check("image is read-only", False)
except OSError:
    check("image is read-only", True)
try:
    p = pathlib.Path.cwd() / ".probe"
    p.write_text("ok")
    p.unlink()
    check("worktree is writable", True)
except OSError as e:
    check("worktree is writable", False, e)
check("runs as non-root", os.getuid() != 0, os.getuid())
print(json.dumps(out))
