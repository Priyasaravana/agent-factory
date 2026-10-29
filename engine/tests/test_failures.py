"""Failure classes (ADR-0043): every rule matches what it is for, and an ordinary work failure
matches none of them."""

from __future__ import annotations

import random

import pytest

from agent_factory import failures
from agent_factory.failures import FailureClass, backoff, classify_agent, classify_command

# The Guestbook run (change df3bdaaeb306): the CLI's text when the gateway key had expired.
GUESTBOOK_401 = (
    "Failed to authenticate. API Error: 401 Authentication Error - Expired Key. Key Expiry time "
    "2026-10-19 23:50:45.548000+00:00 and current time 2026-10-19 23:57:46.012047+00:00"
)

# Failures that are the product's: none may be taken for the factory's.
WORK_AGENT = [
    ("verifier produced no verdict", "Scenario H3 failed: POST /api/messages returned 500 Internal Server Error"),
    ("agent returned no structured output (missing evidence)", "The app answers 401 when the token has expired."),
    ("error_max_turns: ", "I ran out of turns while fixing the rate limiter (429 Too Many Requests)."),
    (None, "curl: (7) Failed to connect: connection refused — the app isn't listening on 8081"),
    ("success: ", "The overloaded endpoint now returns 503 Service Unavailable as the spec asks."),
]


@pytest.mark.parametrize(
    ("error", "text", "rule", "cls", "retryable"),
    [
        ("success: ", GUESTBOOK_401, "key_expired", FailureClass.infra, True),
        (None, "Failed to authenticate. API Error: 401 invalid x-api-key", "auth_failed", FailureClass.infra, True),
        ("model gateway: model gateway unreachable: URLError: refused", "", "gateway_down", FailureClass.infra, True),
        ("model gateway: model gateway refused a key (503)", "", "gateway_down", FailureClass.infra, True),
        (
            "model gateway: model budget for this change reached ($20.10 of $20.00)",
            "",
            "model_budget",
            FailureClass.decision,
            False,
        ),
        (
            "model gateway: model gateway admin key not set (env://LITELLM)",
            "",
            "gateway_setup",
            FailureClass.infra,
            False,
        ),
        ("srt not installed; refusing to run unsandboxed", "", "sandbox_setup", FailureClass.infra, False),
        (
            None,
            'API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}',
            "model_overloaded",
            FailureClass.infra,
            True,
        ),
        (None, "API Error: 500 Internal server error", "model_overloaded", FailureClass.infra, True),
        (None, "API Error: 429 rate_limit_error", "rate_limited", FailureClass.infra, True),
        ("ProcessError: Command failed with exit code 1", "", "call_crashed", FailureClass.infra, True),
        ("agent call did not finish", "", "call_crashed", FailureClass.infra, True),
        ("ConnectionResetError: [Errno 104] Connection reset by peer", "", "network", FailureClass.infra, True),
        ("agent sandbox not ready: image missing", "", "sandbox_not_ready", FailureClass.infra, True),
        ("agent call stalled: nothing for 10 minutes", "", "stalled", FailureClass.infra, True),
    ],
)
def test_agent_rules(error: str | None, text: str, rule: str, cls: FailureClass, retryable: bool) -> None:
    got = classify_agent(error, text)
    assert (got.rule, got.cls, got.retryable) == (rule, cls, retryable)


def test_expired_key_retries_at_once() -> None:
    assert classify_agent("success: ", GUESTBOOK_401).immediate


@pytest.mark.parametrize(("error", "text"), WORK_AGENT)
def test_work_failures_stay_work(error: str | None, text: str) -> None:
    assert classify_agent(error, text) == failures.WORK


def test_network_words_in_the_agents_text_are_not_infra() -> None:
    """The agent describing the app ("connection refused") is evidence about the product."""
    assert classify_agent(None, "ECONNREFUSED when calling the app").cls == FailureClass.work
    assert classify_agent("ECONNREFUSED", "").cls == FailureClass.infra


@pytest.mark.parametrize(
    ("command", "rc", "output", "timed_out", "rule"),
    [
        ("docker build -t a:1 .", 1, "npm ERR! network ECONNRESET", False, "network"),
        ("docker build -t a:1 .", 1, "Cannot connect to the Docker daemon at tcp://dind:2376", False, "docker_daemon"),
        ("kind load docker-image a:1 --name factory", 1, "ERROR: failed to load image: i/o timeout", False, "network"),
        (
            "set -e; helm upgrade --install a deploy/chart --wait --timeout 5m",
            1,
            "Error: Kubernetes cluster unreachable: Get https://x:6443: dial tcp: connect: connection refused",
            False,
            "cluster_unreachable",
        ),
        ("KUBECONFIG=/tmp/k kubectl get pods", 1, "Unable to connect to the server: EOF", False, "cluster_unreachable"),
        ("kubectl rollout status deploy/a", 1, "pod a: ErrImagePull", False, "image_pull"),
        (
            "docker run --rm aquasec/trivy:latest image a:1",
            125,
            "docker: Error response from daemon",
            False,
            "docker_run_daemon",
        ),
        (  # book list, 2026-10-22: the Mac slept during Trivy's DB download
            "docker run --rm -v trivy-cache:/root/.cache aquasec/trivy:latest image --format json a:1 > /tmp/r.json",
            1,
            "FATAL\tFatal error\trun error: init error: DB error: failed to download vulnerability DB: OCI artifact "
            "error: failed to download artifact from mirror.gcr.io/aquasec/trivy-db:2: copy error: unexpected EOF",
            False,
            "scanner_db",
        ),
        (
            "docker run --rm aquasec/trivy:latest image a:1",
            1,
            "Error response from daemon: toomanyrequests",
            False,
            "image_pull",
        ),
        ("helm upgrade --install a deploy/chart", -1, "timed out after 420s", True, "command_timeout"),
        ("git push origin main", 128, "fatal: unable to access: Could not resolve host: github.com", False, "network"),
    ],
)
def test_command_rules(command: str, rc: int, output: str, timed_out: bool, rule: str) -> None:
    got = classify_command(command, rc, output, timed_out, untrusted=False)
    assert (got.cls, got.rule, got.retryable) == (FailureClass.infra, rule, True)


@pytest.mark.parametrize(
    ("command", "rc", "output", "timed_out"),
    [
        # the app's own build fails: the product's
        ("docker build -t a:1 .", 1, "Type error: Property 'x' does not exist on type 'Y'.", False),
        # helm waited and the pods never became ready: usually the app crashing
        ("helm upgrade --install a deploy/chart --wait", 1, "Error: UPGRADE FAILED: context deadline exceeded", False),
        # a scanner's findings, whatever its JSON says
        ("docker run --rm osv-scanner --format json /src", 1, '{"summary": "unexpected EOF ... i/o timeout"}', False),
        ("docker run --rm zricethezav/gitleaks:v8 detect --exit-code 1", 1, "leaks found: 3", False),
        # Trivy found vulnerabilities (exit 1): the product's, even with a DB note in its log
        (
            "docker run --rm aquasec/trivy:latest image --exit-code 1 a:1",
            1,
            "INFO Need to update DB\nTotal: 3 (HIGH: 3)",
            False,
        ),
        # the health-check loop: the app not answering is the product's
        (
            "for i in $(seq 1 30); do curl -fsS http://dind:8081/api/healthz && exit 0; sleep 2; done; exit 1",
            1,
            "",
            False,
        ),
        ("for i in $(seq 1 30); do curl -fsS http://dind:8081/healthz; done", -1, "timed out after 90s", True),
    ],
)
def test_command_work_stays_work(command: str, rc: int, output: str, timed_out: bool) -> None:
    assert classify_command(command, rc, output, timed_out, untrusted=False).cls == FailureClass.work


def test_untrusted_commands_are_always_work() -> None:
    """`make verify` and `make e2e` are the agents' code: even a timeout is the product's."""
    assert classify_command("make verify", -1, "timed out after 900s", True, untrusted=True).cls == FailureClass.work
    assert classify_command("docker build .", 1, "ECONNRESET", False, untrusted=True).cls == FailureClass.work


def test_rules_read_only_the_tail() -> None:
    noisy = "connection reset by peer\n" + "x" * (failures.COMMAND_TAIL + 10)
    assert classify_command("docker build .", 1, noisy, False, untrusted=False).cls == FailureClass.work


def test_backoff_stays_within_the_schedule() -> None:
    rnd = random.Random(7)
    schedule = [20.0, 90.0, 300.0]
    waits = [backoff(n, schedule, rnd) for n in (1, 2, 3, 4)]
    assert 10 <= waits[0] <= 20 and 45 <= waits[1] <= 90 and 150 <= waits[2] <= 300 and 150 <= waits[3] <= 300
    assert backoff(1, []) == 0.0


def test_a_command_cut_off_by_host_sleep_is_infra_and_retried_at_once():
    f = failures.host_asleep(3867)
    assert (f.cls, f.rule, f.retryable, f.immediate) == (FailureClass.infra, "host_asleep", True, True)
    assert "asleep for 1:04:27 during the command" in f.cause
    note = failures.awake_note(3867)
    assert note.startswith("\n[factory] An earlier run of this command was cut off")
    assert "ran it again with the host awake" in note and "real failure" in note


def test_a_failure_depends_only_on_the_environment_its_station_uses():
    local = {"registry": "local", "deploy": "local", "scan": "local", "publish": "github"}
    # code the agents wrote, in a station that may only use the sandbox: the disk and the sandbox
    assert failures.needed_checks(frozenset({"run-sandbox", "write-code"}), True, local) == {"disk", "sandbox"}
    # deploy to any environment, from the engine: the disk and the deploy integration
    assert failures.needed_checks(frozenset({"deploy:*"}), False, local) == {"disk", "integration:local"}
    assert failures.needed_checks(frozenset({"open-pr"}), False, local) == {"disk", "integration:github"}
    # a cloud environment brings its own integrations
    cloud = {"registry": "ecr", "deploy": "eks", "scan": "local", "publish": "github"}
    assert failures.needed_checks(frozenset({"push-image", "deploy:prod"}), False, cloud) == {
        "disk",
        "integration:ecr",
        "integration:eks",
    }
    assert failures.needed_checks(None, True, cloud) == {
        "disk",
        "sandbox",
        "integration:ecr",
        "integration:eks",
        "integration:local",
        "integration:github",
    }


def test_what_is_down_is_named_and_what_was_checked_is_listed():
    from agent_factory.models import CheckView

    checks = [
        CheckView(id="sandbox", title="Agent sandbox", area="agents", state="ready"),
        CheckView(id="disk", title="Docker disk space", area="build", state="degraded", reasons=["91% full"]),
        CheckView(
            id="integration:local",
            title="local (local): registry, deploy",
            area="registry",
            state="failed",
            reasons=["kind cluster API not ready: connection refused"],
        ),
        CheckView(id="ports", title="Free app port", area="deploy", state="failed", reasons=["none free"]),
    ]
    down, checked = failures.env_problems(checks, {"sandbox", "disk", "integration:local"})
    assert down == ["local (local): registry, deploy: kind cluster API not ready: connection refused"]
    assert checked == ["Agent sandbox", "Docker disk space", "local (local): registry, deploy"]
    assert failures.env_problems(checks, {"sandbox"}) == ([], ["Agent sandbox"]), "not needed: not counted"
    f = failures.env_unavailable([*down, "Agent sandbox: not running"])
    assert (f.cls, f.rule, f.retryable, f.immediate) == (FailureClass.infra, "env_unavailable", True, False)
    assert f.cause == (
        "the environment is not available: local (local): registry, deploy: kind cluster API not ready: "
        "connection refused (and 1 more)"
    )
    note = failures.env_healthy_note(checked)
    assert "checked what the command depends on (Agent sandbox; Docker disk space; local" in note
    assert note.endswith("it is all available: treat this as a real failure.")
