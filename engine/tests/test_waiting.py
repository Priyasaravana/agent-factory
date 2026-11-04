"""Waiting and budgets, minimum (roadmap "Now" item 3: L-5, UX-15, UX-18, UX-19). `waiting.py` is pure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import PRODUCT, wait_run

from agent_factory import waiting
from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.runner import AgentRequest, AgentResult
from agent_factory.models import ChangeStatus, EventKind

NOW = datetime(2026, 11, 4, 20, 23, tzinfo=UTC)


def test_the_providers_words_win_over_the_window() -> None:
    # UX-18: the CLI said "temporarily limiting requests (not your usage limit)"; the factory said quota
    text = "API Error: Request rejected (429) · we're temporarily limiting requests (not your usage limit)"
    assert waiting.classify_limit("five_hour", text) == waiting.Limit("throttle")
    assert waiting.classify_limit("five_hour", "") == waiting.Limit("quota", "five-hour")
    assert waiting.classify_limit("seven_day_opus") == waiting.Limit("quota", "weekly Opus")
    assert waiting.classify_limit(None, "rate limited") == waiting.Limit("throttle"), "no window: no known time"


def test_a_quota_waits_for_the_providers_reset_or_says_it_guesses() -> None:
    resets = int((NOW + timedelta(minutes=90)).timestamp())
    p = waiting.plan_pause(waiting.Limit("quota", "five-hour"), resets, NOW, 0)
    assert p.known and p.resume_at == NOW + timedelta(minutes=90) and not p.give_up
    assert (
        p.summary()
        == "paused: the five-hour usage limit of the subscription is reached; resets at 21:53 (from the provider)"
    )

    guess = waiting.plan_pause(waiting.Limit("quota", "five-hour"), None, NOW, 0)
    assert not guess.known and guess.resume_at == NOW + timedelta(minutes=30)
    assert guess.summary().endswith("retrying at about 20:53 (estimate)")

    past = waiting.plan_pause(waiting.Limit("quota"), int((NOW - timedelta(minutes=5)).timestamp()), NOW, 0)
    assert past.resume_at == NOW, "a reset already passed wakes at once"


def test_a_throttle_backs_off_never_borrows_the_quota_reset_and_gives_up_bounded() -> None:
    quota_reset = int((NOW + timedelta(hours=3)).timestamp())
    waits = []
    n = 0
    for _ in range(waiting.MAX_THROTTLES):
        p = waiting.plan_pause(waiting.Limit("throttle"), quota_reset, NOW, n)
        assert not p.known and not p.give_up and p.kind == "throttle"
        assert "(not the usage quota)" in p.summary() and "(estimate" in p.summary()
        waits.append(int((p.resume_at - NOW).total_seconds() // 60))
        n = p.throttles
    assert waits == [2, 4, 8, 16, 30, 30, 30, 30]
    last = waiting.plan_pause(waiting.Limit("throttle"), quota_reset, NOW, n)
    assert last.give_up and "still rate limiting after 8 pauses" in last.summary()


def test_the_meters_disagree_only_when_far_apart() -> None:
    # UX-15: the architect's call, $0.81 on the CLI's meter, $5.00 on the gateway's
    note = waiting.disagree(0.81, 5.00)
    assert note == "the two cost meters disagree: agent CLI $0.81, gateway $5.00 (the gateway 6.2× the other)"
    assert waiting.disagree(0.81, None) is None, "direct: one meter"
    assert waiting.disagree(1.00, 1.50) is None, "under 2×"
    assert waiting.disagree(0.01, 0.10) is None, "cents"
    assert "agent CLI 3.0×" in (waiting.disagree(3.0, 1.0) or "")


def test_usage_keeps_tokens_per_model_for_reconciling() -> None:
    raw = {
        "claude-opus": {
            "inputTokens": 1200,
            "outputTokens": 9000,
            "cacheReadInputTokens": 2_400_000,
            "cacheCreationInputTokens": 80_000,
            "costUSD": 0.8123456,
            "contextWindow": 200000,
        },
        "bad": "not a dict",
    }
    assert waiting.usage_summary(raw) == {  # type: ignore[arg-type]
        "claude-opus": {
            "input": 1200.0,
            "output": 9000.0,
            "cache_read": 2_400_000.0,
            "cache_write": 80_000.0,
            "cost_usd": 0.8123,
        }
    }
    assert waiting.usage_summary(None) == {}


def test_a_budget_hold_leads_with_the_budget() -> None:
    text = waiting.budget_hold(
        waiting.BudgetUse(
            scope="call",
            cap_usd=5.0,
            call_cli_usd=0.81,
            call_gateway_usd=5.0,
            change_cli_usd=2.10,
            change_gateway_usd=7.40,
            role="architect",
        )
    )
    lines = text.splitlines()
    assert lines[0].startswith("model budget per agent call reached: the architect call stopped at the gateway's cap")
    assert "model_gateway.call_budget_usd" in lines[0]
    assert lines[1] == "this call: agent CLI $0.81, gateway $5.00"
    assert lines[2] == "this change so far: agent CLI $2.10, gateway $7.40"
    assert "6.2×" in lines[3]
    assert "never raises a cap" in lines[-1]

    change = waiting.budget_hold(waiting.BudgetUse("change", 20.0, 0.0, None, 20.10, None, "developer"))
    assert change.startswith("model budget for this change reached: $20.00 (model_gateway.change_budget_usd")
    assert "gateway" not in change.splitlines()[2], "direct: the gateway meter isn't invented"


class OverCallBudget(FakeAgentRunner):
    """The architect's call is stopped by the gateway's per-call cap, as in UX-15."""

    async def run(self, req: AgentRequest, sink):  # noqa: ANN001, ANN201
        if req.role == "architect":
            self.calls.append(req)
            return AgentResult(
                ok=False,
                cost_usd=0.81,
                turns=30,
                text='API Error: 400 {"error":{"message":"Budget has been exceeded! Current cost: 5.0, '
                'Max budget: 5.0","type":"budget_exceeded"}}',
                error="success: ",
                gateway={"provider": "litellm", "key_alias": "k", "spend_usd": 5.0, "revoked": True},
            )
        return await super().run(req, sink)


async def test_a_call_over_its_budget_holds_naming_the_budget_and_both_meters(make_factory, cfg):
    cfg.model_gateway.provider = "litellm"  # the fake runner never opens it; the hold reads the config
    f = make_factory(agents=OverCallBudget())
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert held.hold_class == "decision", "a person's call, not routed to repair"
    assert held.summary.startswith("held at design: model budget per agent call reached")
    assert "agent CLI $0.81, gateway $5.00" in (held.last_failure or "")
    assert held.gateway_spend_usd == 5.0 and held.cost_usd >= 0.81
    started = [e.station for e in f.store.list_events(change.id) if e.kind == EventKind.station_started]
    assert started.count("design") == 1, "never retried"
    calls = [e for e in f.store.list_events(change.id) if "agent_call" in e.data]
    assert any("the two cost meters disagree" in e.message for e in calls)
