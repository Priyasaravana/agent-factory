"""The deployed app's first admin signs the live checks in (ADR-0044 §3).

The stack pack's chart generates the admin into a Kubernetes Secret; after deploy the acceptance
station reads it (privileged, not recorded) and gives it to the pack's e2e suite and to the
verifier through the environment, by name. The values are redacted everywhere they are stored."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from conftest import PRODUCT, wait_run

from agent_factory.agents.fake import FakeAgentRunner
from agent_factory.agents.runner import AgentRequest
from agent_factory.config import SandboxConfig
from agent_factory.engine.stations import ADMIN_EMAIL_ENV, ADMIN_PASSWORD_ENV
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.providers.base import Readiness, secret_data
from agent_factory.sandbox import SandboxManager, wrapper
from agent_factory.sandbox.spec import SandboxSpec
from agent_factory.secret_refs import MASK, REDACTOR

WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})
PASSWORD = "chartchartchartchart"  # stands for what the chart generates (low entropy: no scan noise)


def _secret_json(email: str = "admin@example.com", password: str = PASSWORD) -> str:
    enc = {k: base64.b64encode(v.encode()).decode() for k, v in {"email": email, "password": password}.items()}
    return json.dumps({"apiVersion": "v1", "kind": "Secret", "data": enc})


class RecordingExecutor(FakeExecutor):
    """A fake executor that also keeps the environment each command was given."""

    def __init__(self, **kw) -> None:  # noqa: ANN003
        super().__init__(**kw)
        self.envs: list[tuple[str, dict[str, str]]] = []

    async def run(self, command, cwd=None, timeout=600, env=None) -> CommandResult:  # noqa: ANN001
        self.envs.append((command, dict(env or {})))
        return await super().run(command, cwd, timeout, env)


# ----------------------------------------------------------------- reading --
def test_a_secret_is_decoded_and_its_values_redacted():
    data = secret_data(_secret_json(password="uniqueuniqueunique"))
    assert data == {"email": "admin@example.com", "password": "uniqueuniqueunique"}
    assert REDACTOR.knows("uniqueuniqueunique")
    assert REDACTOR.text("pw=uniqueuniqueunique") == f"pw={MASK}"


def test_what_is_not_a_secret_reads_as_none():
    assert secret_data('Error from server (NotFound): secrets "x" not found') is None
    assert secret_data('{"kind": "Secret"}') is None
    assert secret_data('{"data": {"bad": "%%%"}}') == {}


# -------------------------------------------------------------- in the lane --
async def test_e2e_and_the_verifier_sign_in_as_the_apps_admin(make_factory):
    ex = RecordingExecutor(scan_outputs={"get secret": _secret_json()})
    agents = FakeAgentRunner()
    f = make_factory(ex, agents)
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback

    [read] = [c for c in ex.calls if "get secret" in c]
    assert f"get secret {product.slug}-admin -o json" in read, "the pack names the Secret"
    live = [env for cmd, env in ex.envs if cmd.startswith("make e2e BASE_URL=http://dind")]
    assert live and live[-1] == {ADMIN_EMAIL_ENV: "admin@example.com", ADMIN_PASSWORD_ENV: PASSWORD}
    preview = [env for cmd, env in ex.envs if "make preview" in cmd]
    assert preview and all(ADMIN_PASSWORD_ENV not in env for env in preview), "the preview has its own test admin"

    [verifier] = [r for r in agents.calls if r.station == "acceptance"]
    assert verifier.secret_env[ADMIN_PASSWORD_ENV] == PASSWORD
    assert f"${ADMIN_PASSWORD_ENV}" in verifier.prompt and PASSWORD not in verifier.prompt
    assert "/api/auth/sign-in" in verifier.prompt, "the pack says how its app signs in"

    events = f.store.list_events(change.id)
    stored = json.dumps([[e.message, e.data] for e in events])
    assert PASSWORD not in stored, "never in the event log"
    assert not any("get secret" in e.message for e in events), "the read is not recorded"
    assert any(e.message.startswith("e2e and the verifier sign in as the app's admin") for e in events)


async def test_without_an_admin_in_the_cluster_the_checks_run_signed_out(make_factory):
    ex = RecordingExecutor()  # the fake cluster has no such Secret
    agents = FakeAgentRunner()
    f = make_factory(ex, agents)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    live = [env for cmd, env in ex.envs if cmd.startswith("make e2e BASE_URL=http://dind")]
    assert live and all(ADMIN_PASSWORD_ENV not in env for env in live)
    [verifier] = [r for r in agents.calls if r.station == "acceptance"]
    assert verifier.secret_env == {} and ADMIN_PASSWORD_ENV not in verifier.prompt


async def test_a_stack_without_an_admin_secret_never_reads_one(make_factory):
    ex = RecordingExecutor(scan_outputs={"get secret": _secret_json()})
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))  # FastAPI: no admin_secret
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert not any("get secret" in c for c in ex.calls)


# ---------------------------------------------------------------- sandboxes --
HOME = Path(__file__).resolve().parents[2]


async def test_a_secret_reaches_a_sandboxed_command_by_name_never_in_argv(tmp_path: Path):
    ex = RecordingExecutor()
    mgr = SandboxManager(SandboxConfig(), HOME, tmp_path, ex=ex)
    mgr.state = Readiness("ready", ["test"])
    REDACTOR.add("sandboxsandboxsandbox")
    env = {"BASE": "http://x", ADMIN_PASSWORD_ENV: "sandboxsandboxsandbox"}
    await mgr.executor("r1", "acceptance").run("make e2e", cwd=tmp_path, env=env)
    cmd, outer = ex.envs[-1]
    assert "sandboxsandboxsandbox" not in cmd
    assert f"-e {ADMIN_PASSWORD_ENV} " in cmd and "-e BASE=http://x" in cmd
    assert outer == {ADMIN_PASSWORD_ENV: "sandboxsandboxsandbox"}, "docker inherits it from its environment"


def test_an_agent_session_inherits_only_the_secrets_it_was_given():
    spec = SandboxSpec(name="n", image="img:1", network="net", workdir="/w", allow_env=[ADMIN_PASSWORD_ENV])
    env = {ADMIN_PASSWORD_ENV: "x" * 20, "DB_PASSWORD": "y" * 20, "CLAUDE_CODE_OAUTH_TOKEN": "t"}
    argv = wrapper.build_argv(spec.to_json(), ["-p"], env)
    assert ADMIN_PASSWORD_ENV in argv and "DB_PASSWORD" not in argv
    assert "x" * 20 not in " ".join(argv), "by name: the value stays in the environment"
    plain = SandboxSpec(name="n", image="img:1", network="net", workdir="/w")
    assert ADMIN_PASSWORD_ENV not in wrapper.build_argv(plain.to_json(), ["-p"], env), "names that look secret stay out"


def test_an_agent_request_carries_no_secret_by_default(tmp_path: Path):
    req = AgentRequest(change_id="c", station="acceptance", role="verifier", prompt="p", cwd=tmp_path, model="m")
    assert req.secret_env == {}
