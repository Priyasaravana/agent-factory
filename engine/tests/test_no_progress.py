"""The no-progress stop and the e2e target probe (ADR-0043 follow-up, roadmap J1).

Book lending (change 66697b75071c, 2026-10-25): live e2e failed three times with
`getaddrinfo ENOTFOUND dind`. The classifier called it work, so it went to the developer, who twice
changed nothing ("the failure is an environment problem"); the same failure came back each time and
the loop ran until build's attempt budget held the change, after ~$4.90.
- The probe: before live e2e, acceptance checks that the sandbox reaches the app the way the test
  client does. Unreachable is infra: retried in place, then held, never sent to the developer.
- The no-progress stop: the same failure at the same station on the same code (the repair changed
  nothing) holds with the cause instead of looping.
"""

from __future__ import annotations

from conftest import PRODUCT, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.runner import AgentRequest
from agent_factory.engine.stations import is_loopback, target_probe_command
from agent_factory.executor import FakeExecutor
from agent_factory.failures import failure_fingerprint
from agent_factory.models import ChangeStatus, EventKind, RoutedFailure

WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})
LIVE_E2E = "make e2e BASE_URL=http://dind"  # the live run, not the preview (127.0.0.1)
PROBE = "curl --fail --silent --show-error"
ENOTFOUND = """Running 12 tests using 1 worker
  ✘  1 [chromium] › e2e/home.spec.ts:4:7 › @R1 home page loads (1.2s)
    Error: page.goto: net::ERR_NAME_NOT_RESOLVED at http://dind:8084/
    getaddrinfo ENOTFOUND dind
  12 failed
make: *** [Makefile:31: e2e] Error 1"""


def _quick(f, **budgets):
    b = f.manager.cfg.budgets
    b.infra_backoff_seconds = [0.0]
    for k, v in budgets.items():
        setattr(b, k, v)
    return f


class IdleRepairs(FakeAgentRunner):
    """A developer that builds the product, then changes nothing when sent a failure to fix,
    and says why (as the Book lending developer did)."""

    async def run(self, req: AgentRequest, sink):  # type: ignore[no-untyped-def]
        if req.role == "developer" and "Fix this failure" in req.prompt:
            self.calls.append(req)
            await sink("agent", {"text": "I made no code changes. The failure is an environment problem."})
            from agent_factory.agents.runner import AgentResult

            return AgentResult(ok=True, text="I made no code changes. The failure is an environment problem.")
        return await super().run(req, sink)


def _developer_calls(agents: FakeAgentRunner) -> int:
    return [c.role for c in agents.calls].count("developer")


# ------------------------------------------------------------------- pure --
def test_the_same_failure_on_a_later_run_has_the_same_fingerprint() -> None:
    later = ENOTFOUND.replace("(1.2s)", "(3.9s)").replace("8084", "8091").replace("Makefile:31", "Makefile:32")
    coloured = "\x1b[31m" + ENOTFOUND + "\x1b[0m"
    assert failure_fingerprint(ENOTFOUND) == failure_fingerprint(later) == failure_fingerprint(coloured)
    assert failure_fingerprint("image 22dcafa1 failed") == failure_fingerprint("image 9be01f3c failed")


def test_a_different_failure_has_a_different_fingerprint() -> None:
    other = ENOTFOUND.replace("getaddrinfo ENOTFOUND dind", "expect(received).toBe(expected)")
    assert failure_fingerprint(ENOTFOUND) != failure_fingerprint(other)
    assert failure_fingerprint("") == failure_fingerprint("   ")


def test_only_the_same_failure_on_the_same_code_is_no_progress() -> None:
    before = RoutedFailure(station="acceptance", fingerprint="f1", tree="t1")
    assert before.repeated_by(RoutedFailure(station="acceptance", fingerprint="f1", tree="t1"))
    assert not before.repeated_by(RoutedFailure(station="acceptance", fingerprint="f1", tree="t2")), "code changed"
    assert not before.repeated_by(RoutedFailure(station="acceptance", fingerprint="f2", tree="t1")), "new failure"
    assert not before.repeated_by(RoutedFailure(station="test", fingerprint="f1", tree="t1")), "another station"
    unknown = RoutedFailure(station="acceptance", fingerprint="f1", tree="")
    assert not unknown.repeated_by(unknown), "git couldn't say: never stop on a guess"


def test_the_probe_reaches_the_app_the_way_the_test_client_does() -> None:
    proxied = target_probe_command("http://dind:8084", "/api/healthz")
    assert proxied.startswith(PROBE) and proxied.endswith(" http://dind:8084/api/healthz")
    assert '${HTTP_PROXY:+--proxy "$HTTP_PROXY"}' in proxied and "--noproxy" not in proxied
    direct = target_probe_command("http://127.0.0.1:3000/", "healthz")
    assert "--noproxy '*'" in direct and "--proxy" not in direct.replace("--noproxy", "")
    assert direct.endswith(" http://127.0.0.1:3000/healthz")
    assert is_loopback("http://localhost:3000") and is_loopback("http://[::1]:3000")
    assert not is_loopback("http://dind:8084") and not is_loopback("https://app.example.com")


# --------------------------------------------------------------- pipeline --
async def test_book_lending_the_same_failure_after_an_empty_repair_holds_with_the_cause(make_factory):
    ex = FakeExecutor(fail_on=[LIVE_E2E] * 3, fail_with={LIVE_E2E: ENOTFOUND})
    agents = IdleRepairs()
    f = _quick(make_factory(ex, agents))
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.held

    held = f.store.get_change(change.id)
    assert held.current_station == "acceptance" and held.hold_class == "work"
    assert held.summary.startswith("held at acceptance: no progress: acceptance failed the same way again")
    assert "ENOTFOUND dind" in held.last_failure
    assert "--- what implement said ---" in held.last_failure and "environment problem" in held.last_failure
    assert _developer_calls(agents) == 2, "built once, one repair: not a loop until a budget runs out"
    assert len([c for c in ex.calls if c.startswith(LIVE_E2E)]) == 2
    events = f.store.list_events(change.id)
    assert any(e.message == "implement: passed — the developer changed no code for this failure" for e in events)
    [stop] = [e.data["no_progress"] for e in events if "no_progress" in (e.data or {})]
    assert stop["station"] == "acceptance" and stop["repair"] == "implement" and stop["tree"]
    assert not any("exceeded its attempt budget" in e.message for e in events)

    # a person fixed the environment and resumes: the third failure is routed once more (resume
    # forgets the last one), and the fourth e2e run passes
    f.manager.resume(change.id)
    assert f.store.get_change(change.id).last_routed is None
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert _developer_calls(agents) == 3


async def test_a_repair_that_changes_code_keeps_looping_as_before(make_factory):
    """The stop only takes away loops in which nothing changed: the fake developer edits a file on
    every pass, so the same failure twice is still routed, and the third run passes."""
    ex = FakeExecutor(fail_on=[LIVE_E2E] * 2, fail_with={LIVE_E2E: ENOTFOUND})
    agents = FakeAgentRunner()
    f = _quick(make_factory(ex, agents))
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert _developer_calls(agents) == 3
    assert not any("no_progress" in (e.data or {}) for e in f.store.list_events(change.id))


async def test_an_unreachable_e2e_target_is_infra_never_sent_to_the_developer(make_factory):
    curl_6 = "curl: (6) Could not resolve host: dind"
    ex = FakeExecutor(fail_on=[PROBE] * 4, fail_with={PROBE: curl_6})
    agents = FakeAgentRunner()
    f = _quick(make_factory(ex, agents), infra_retries=3)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.held

    held = f.store.get_change(change.id)
    assert held.current_station == "acceptance" and held.hold_class == "infra"
    assert "infrastructure: the sandbox cannot reach the app at http://dind:" in held.summary
    assert "through the egress proxy" in held.summary and "after 3 retries" in held.summary
    assert curl_6 in held.last_failure
    assert _developer_calls(agents) == 1, "nothing went to the developer"
    assert not any(c.startswith(LIVE_E2E) for c in ex.calls), "e2e never ran against a target it can't reach"
    assert [c.role for c in agents.calls].count("verifier") == 0
    retries = [e.data["retry"] for e in f.store.list_events(change.id) if "retry" in (e.data or {})]
    assert [(r["rule"], r["attempt"]) for r in retries] == [("e2e_target_unreachable", n) for n in (1, 2, 3)]
    assert held.attempts.get("acceptance", 0) == 0, "the factory's failure is not an attempt"


async def test_a_probe_that_reaches_the_app_after_a_retry_goes_on(make_factory):
    ex = FakeExecutor(fail_on=[PROBE], fail_with={PROBE: "curl: (7) Failed to connect to dind port 8084"})
    f = _quick(make_factory(ex))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))  # FastAPI: the probe is for every pack
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    probes = [c for c in ex.calls if c.startswith(PROBE)]
    assert len(probes) == 2 and all(c.endswith("/healthz") for c in probes)
    events = f.store.list_events(change.id)
    assert any(e.kind == EventKind.log and e.message.startswith("the sandbox reaches the app at") for e in events)
