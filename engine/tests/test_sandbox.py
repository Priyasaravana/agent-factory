"""Agent sandbox (ADR-0014): container spec, env filtering, git mounts, the
egress allowlist proxy, the CLI wrapper and where untrusted commands run.
No Docker needed: container commands are recorded, not executed."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from conftest import ORDER, wait_run

from agent_factory.agents.runner import AgentRequest, ClaudeAgentRunner
from agent_factory.config import SandboxConfig
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import RunStatus
from agent_factory.providers.base import Readiness
from agent_factory.sandbox import SandboxManager, egress, wrapper
from agent_factory.sandbox.spec import Mount, SandboxSpec, forwarded_env_names, git_mounts

REPO_ROOT = Path(__file__).resolve().parents[2]

ENGINE_ENV = {
    "CLAUDE_CODE_OAUTH_TOKEN": "model-token",
    "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
    "CLAUDE_AGENT_SDK_VERSION": "0.2",
    "GITHUB_TOKEN": "gh-secret",
    "SKILLS_GITHUB_TOKEN": "skills-secret",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "DOCKER_HOST": "tcp://docker:2376",
    "DOCKER_CERT_PATH": "/home/factory/.docker/certs",
    "KUBECONFIG": "/data/kube/config",
    "FACTORY_ADMIN_PASSWORD": "pw",
    "PATH": "/usr/bin",
}


def test_only_model_and_sdk_variables_reach_the_sandbox():
    names = forwarded_env_names(ENGINE_ENV)
    assert names == ["CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_OAUTH_TOKEN"]


def test_container_is_hardened_and_secrets_never_appear_in_argv():
    spec = SandboxSpec(
        name="af-sbx-r1-build",
        image="agent-factory-sandbox:abc",
        network="factory-sandbox",
        workdir="/data/runs/r1",
        mounts=[Mount("/data/runs/r1", "/data/runs/r1", readonly=False)],
        pass_env=forwarded_env_names(ENGINE_ENV),
    )
    argv = spec.argv(["claude", "--print"], interactive=True)
    joined = " ".join(argv)
    for flag in (
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--user 10001:10001",
        "--network factory-sandbox",
    ):
        assert flag in joined
    assert "-i" in argv and argv[-3:] == ["agent-factory-sandbox:abc", "claude", "--print"]
    assert "type=bind,src=/data/runs/r1,dst=/data/runs/r1" in joined and "/data/runs/r1,readonly" not in joined
    assert "model-token" not in joined, "secret values are inherited by name, never put on the command line"
    assert "-e CLAUDE_CODE_OAUTH_TOKEN" in joined
    for leak in ("GITHUB_TOKEN", "DOCKER_HOST", "KUBECONFIG", "/var/run/docker.sock", "/certs"):
        assert leak not in joined
    assert SandboxSpec.from_json(spec.to_json()) == spec


def test_git_worktree_metadata_is_read_only_except_its_own_index(tmp_path):
    repo, wt = tmp_path / "products" / "p", tmp_path / "runs" / "r1"
    repo.mkdir(parents=True)
    run = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main", cwd=repo)
    (repo / "f").write_text("x")
    run("add", "f", cwd=repo)
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=repo)
    run("worktree", "add", "-q", "-b", "run/r1", str(wt), "main", cwd=repo)
    common, admin = git_mounts(wt)
    assert common == Mount(str((repo / ".git").resolve()), str((repo / ".git").resolve()), True)
    assert admin.source.endswith(".git/worktrees/r1") and admin.readonly is False
    assert git_mounts(repo) == [], "a normal repo (not a worktree) needs no extra mounts"


# ------------------------------------------------------------------ egress --
@pytest.mark.parametrize(
    ("rule", "host", "port", "ok"),
    [
        ("api.anthropic.com:443", "api.anthropic.com", 443, True),
        ("api.anthropic.com:443", "API.anthropic.com.", 443, True),
        ("api.anthropic.com:443", "api.anthropic.com", 80, False),
        ("api.anthropic.com:443", "evil-api.anthropic.com", 443, False),
        ("*.pythonhosted.org:443", "files.pythonhosted.org", 443, True),
        ("*.pythonhosted.org:443", "pythonhosted.org", 443, False),
        ("*.pythonhosted.org:443", "pythonhosted.org.evil.com", 443, False),
        ("dind:8081-8085", "dind", 8083, True),
        ("dind:8081-8085", "dind", 2376, False),
    ],
)
def test_allowlist_rules(rule, host, port, ok):
    assert egress.Allowlist([rule]).allows(host, port) is ok


def test_request_targets_are_parsed_strictly():
    assert egress.parse_target("CONNECT", "api.anthropic.com:443") == ("api.anthropic.com", 443, "")
    assert egress.parse_target("GET", "http://dind:8081/items?x=1") == ("dind", 8081, "/items?x=1")
    assert egress.parse_target("GET", "http://example.com") == ("example.com", 80, "/")
    with pytest.raises(ValueError):
        egress.parse_target("GET", "/relative")


async def _http_upstream() -> tuple[asyncio.Server, int]:
    async def serve(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = await r.readuntil(b"\r\n\r\n")
        body = head.split(b"\r\n")[0]
        w.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
        await w.drain()
        w.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _ask(proxy_port: int, raw: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", proxy_port)
    w.write(raw)
    await w.drain()
    data = await asyncio.wait_for(r.read(4096), 5)
    w.close()
    return data


async def test_egress_proxy_allows_listed_destinations_and_denies_the_rest():
    upstream, up_port = await _http_upstream()
    allow = egress.Allowlist([f"127.0.0.1:{up_port}"])
    proxy = await asyncio.start_server(lambda r, w: egress.handle(r, w, allow), "127.0.0.1", 0)
    pport = proxy.sockets[0].getsockname()[1]
    async with upstream, proxy:
        ok = await _ask(pport, f"GET http://127.0.0.1:{up_port}/items HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert ok.startswith(b"HTTP/1.1 200") and ok.endswith(b"GET /items HTTP/1.1"), "forwarded in origin form"
        denied = await _ask(pport, b"CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n")
        assert denied.startswith(b"HTTP/1.1 403") and b"not on the sandbox allowlist" in denied
        other_port = await _ask(pport, f"GET http://127.0.0.1:{up_port + 1}/ HTTP/1.1\r\n\r\n".encode())
        assert other_port.startswith(b"HTTP/1.1 403")
        # CONNECT tunnel to an allowed destination, then plain bytes through it
        r, w = await asyncio.open_connection("127.0.0.1", pport)
        w.write(f"CONNECT 127.0.0.1:{up_port} HTTP/1.1\r\n\r\n".encode())
        assert (await r.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        w.write(b"GET /tunnelled HTTP/1.1\r\n\r\n")
        await w.drain()
        assert (await asyncio.wait_for(r.read(4096), 5)).endswith(b"GET /tunnelled HTTP/1.1")
        w.close()


def test_proxy_refuses_to_start_without_rules(monkeypatch):
    monkeypatch.delenv("EGRESS_ALLOW", raising=False)
    with pytest.raises(SystemExit):
        egress.main([])


# ----------------------------------------------------------------- wrapper --
def test_wrapper_runs_the_cli_inside_docker_with_filtered_env():
    spec = SandboxSpec(name="n", image="img:1", network="net", workdir="/w")
    argv = wrapper.build_argv(spec.to_json(), ["--output-format", "stream-json"], ENGINE_ENV)
    assert argv[:2] == ["docker", "run"] and argv[-3:] == ["claude", "--output-format", "stream-json"]
    assert "-e" in argv and "GITHUB_TOKEN" not in argv and "CLAUDE_CODE_OAUTH_TOKEN" in argv


def test_wrapper_answers_the_version_probe_and_refuses_without_a_spec(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["agent-factory-sandbox-claude", "-v"])
    wrapper.main()
    assert "(Claude Code)" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["agent-factory-sandbox-claude", "--print"])
    monkeypatch.delenv(wrapper.SPEC_ENV, raising=False)
    with pytest.raises(SystemExit):
        wrapper.main()


# ------------------------------------------------------ manager & executor --
def _ready_manager(tmp_path: Path, ex: FakeExecutor) -> SandboxManager:
    mgr = SandboxManager(SandboxConfig(), Path(__file__).resolve().parents[2], tmp_path, ex=ex)
    mgr.state = Readiness("ready", ["test"])
    return mgr


async def test_untrusted_commands_run_in_a_fresh_sandbox(tmp_path):
    ex = FakeExecutor()
    sbx = _ready_manager(tmp_path, ex).executor("r1", "verify")
    res = await sbx.run("make verify", cwd=tmp_path, timeout=60)
    assert res.ok and res.command == "make verify"
    call = ex.calls[-1]
    assert call.startswith("docker run") and "bash -lc 'make verify'" in call
    assert f"src={tmp_path},dst={tmp_path}" in call and "--network factory-sandbox" in call
    assert "HTTPS_PROXY=http://factory-egress:3128" in call and "af.run=r1" in call


async def test_nothing_runs_while_the_sandbox_is_not_ready(tmp_path):
    ex = FakeExecutor()
    mgr = SandboxManager(SandboxConfig(), tmp_path, tmp_path, ex=ex)
    mgr.state = Readiness("failed", ["image build failed"])
    mgr._ensure = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
    mgr._ensure.set_result(mgr.state)  # type: ignore[union-attr]
    res = await mgr.executor().run("make verify", cwd=tmp_path)
    assert not res.ok and "sandbox not ready" in res.output and ex.calls == []
    runner = ClaudeAgentRunner(tmp_path, sandbox=mgr)
    req = AgentRequest(run_id="r1", station="build", role="developer", prompt="p", cwd=tmp_path, model="m")
    out = await runner.run(req, _noop_sink)
    assert not out.ok and "sandbox not ready" in (out.error or "")


async def _noop_sink(kind, data):  # noqa: ANN001
    return None


async def test_agent_sessions_use_the_wrapper_and_are_removed_afterwards(tmp_path, monkeypatch):
    ex = FakeExecutor()
    mgr = _ready_manager(tmp_path, ex)
    runner = ClaudeAgentRunner(tmp_path, sandbox=mgr)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/local/bin/{name}")
    seen: dict = {}

    async def fake_run(req, sink, transport, plugin_home=None):  # noqa: ANN001, ASYNC240
        seen.update(transport)
        spec = SandboxSpec.from_json(Path(transport["env"]["AF_SANDBOX_SPEC"]).read_text())  # noqa: ASYNC240
        seen["spec"] = spec
        from agent_factory.agents.runner import AgentResult

        return AgentResult(ok=True)

    monkeypatch.setattr(runner, "_run", fake_run)
    events: list = []

    async def sink(kind, data):  # noqa: ANN001
        events.append((kind, data))

    plugin = tmp_path / "imported"
    plugin.mkdir()
    req = AgentRequest(
        run_id="r1", station="build", role="developer", prompt="p", cwd=tmp_path, model="m", imported_plugin=plugin
    )
    assert (await runner.run(req, sink)).ok
    assert seen["cli_path"].endswith("agent-factory-sandbox-claude")
    spec: SandboxSpec = seen["spec"]
    assert Mount(str(tmp_path), str(tmp_path), False) in spec.mounts
    assert Mount(str(plugin), str(plugin), True) in spec.mounts
    assert not any("holdout" in m.source for m in spec.mounts)
    assert events and events[0][0] == "sandbox"
    assert any(c.startswith(f"docker rm -f {spec.name}") for c in ex.calls), "session container removed"
    assert not Path(seen["env"]["AF_SANDBOX_SPEC"]).exists()  # noqa: ASYNC240


async def test_verify_runs_in_the_sandbox_while_engine_steps_do_not(make_factory, tmp_path):
    engine_ex, sandbox_ex = FakeExecutor(), FakeExecutor()
    f = make_factory(engine_ex)
    mgr = _ready_manager(tmp_path, sandbox_ex)
    f.manager.sandbox = mgr
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    assert any("make verify" in c and c.startswith("docker run") for c in sandbox_ex.calls)
    assert not any(c == "make verify" for c in engine_ex.calls), "agent-written tests never run in the engine"
    assert any(c.startswith("docker build") for c in engine_ex.calls), "privileged steps stay in the engine"


def test_health_reports_the_sandbox(make_factory):
    f = make_factory()
    from agent_factory.actions import get_health

    assert get_health(f).sandbox == "off"
    f.sandbox = SandboxManager(SandboxConfig(), Path("."), Path("."))
    f.sandbox.state = Readiness("failed", ["no docker"])
    h = get_health(f)
    assert (h.sandbox, h.sandbox_detail) == ("failed", ["no docker"])


def test_default_config_enables_the_sandbox_with_a_narrow_allowlist(cfg):
    assert cfg.sandbox.mode == "container"
    assert "api.anthropic.com:443" in cfg.sandbox.egress
    assert not any("github" in r for r in cfg.sandbox.egress), "agents never reach GitHub directly"
    assert json.loads(json.dumps(cfg.sandbox.model_dump()))["network"] == "factory-sandbox"


def test_command_result_shape_is_unchanged():
    assert CommandResult("x", 0, "").ok


async def test_package_cache_volume_is_handed_to_the_sandbox_user(tmp_path):
    ex = FakeExecutor()
    mgr = SandboxManager(SandboxConfig(), REPO_ROOT, tmp_path, ex=ex)
    assert await mgr._cache() is None
    create, chown = ex.calls[-2:]
    assert create.startswith("docker volume create") and "factory-sandbox-cache" in create
    assert "--user 0" in chown and "os.chown" in chown and "10001" in chown and "--network none" in chown
