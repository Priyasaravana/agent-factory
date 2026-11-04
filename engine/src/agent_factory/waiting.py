"""Waiting and budgets, minimum (roadmap "Now" item 3; live-test findings L-5, UX-15, UX-18, UX-19).

Pure. Three rules that keep a pause or a budget hold honest about what it is waiting on:

1. **Quota or throttle (UX-18).** A provider can refuse a call for two different reasons. A spent
   subscription window (`five_hour`, `seven_day`, ...) resets at a time the provider states. A
   throttle ("temporarily limiting requests, not your usage limit") passes on its own; the provider
   gives no time for it, so any time shown is the factory's estimate and is labelled so. Throttles
   back off (2, 4, 8, 16, 30 minutes) and stop after `MAX_THROTTLES` pauses in a row: then a person
   looks, rather than the factory waking forever.
2. **Two meters (L-5).** The agent CLI prices each call itself (`cost_usd`, Anthropic list prices);
   with a gateway (ADR-0036) the gateway prices it too (`spend_usd`), and the gateway's figure is the
   one its per-call cap is enforced on. A change records both; neither is silently taken for the
   other. `disagree` says when they are far enough apart that a forecast on one is not a forecast on
   the other.
3. **A budget hold names the budget (UX-15):** which cap (per call or per change), where it is set,
   the cap, the usage on both meters, and what a person can do. A cap is never raised or dropped by
   the factory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

LimitKind = Literal["quota", "throttle"]

# subscription usage windows the CLI reports (`RateLimitInfo.rate_limit_type`)
QUOTA_WINDOWS = {
    "five_hour": "five-hour",
    "seven_day": "weekly",
    "seven_day_opus": "weekly Opus",
    "seven_day_sonnet": "weekly Sonnet",
    "overage": "extra-usage",
}
# the CLI's wording when the provider throttles rather than refuses for quota
THROTTLE_TEXT = re.compile(r"not your usage limit|temporarily limiting requests", re.IGNORECASE)
THROTTLE_MINUTES = (2, 4, 8, 16, 30)  # the last step repeats
MAX_THROTTLES = 8  # throttle pauses in a row before a person is asked to look (about 2.5 hours)
QUOTA_FALLBACK_MINUTES = 30  # a quota refusal without a reset time: an estimate, labelled as one


@dataclass(frozen=True)
class Limit:
    kind: LimitKind
    window: str | None = None  # quota: which window, as a person says it ("five-hour")


def classify_limit(rate_limit_type: str | None, text: str = "") -> Limit:
    """What a refused call is waiting on. The provider's own words win: a refusal that says it is not
    the usage limit is a throttle whatever else came with it. A refusal without a known window is a
    throttle too: its time is unknown either way, and a short wake that is refused again costs only
    that refusal."""
    if THROTTLE_TEXT.search(text or ""):
        return Limit("throttle")
    if rate_limit_type in QUOTA_WINDOWS:
        return Limit("quota", QUOTA_WINDOWS[rate_limit_type])
    return Limit("throttle")


@dataclass(frozen=True)
class PausePlan:
    kind: LimitKind
    resume_at: datetime
    known: bool  # True: the provider stated the time; False: the factory's estimate
    throttles: int  # throttle pauses in a row, this one included (0 for quota)
    window: str | None
    give_up: bool  # throttled MAX_THROTTLES times in a row: hold for a person instead

    def summary(self) -> str:
        at = f"{self.resume_at:%H:%M}"
        if self.give_up:
            return (
                f"the model provider is still rate limiting after {self.throttles - 1} pauses in a row "
                "(not the usage quota); resume to try again, or check the provider's status"
            )
        if self.kind == "quota":
            what = f"the {self.window} usage limit of the subscription is reached" if self.window else "usage limit"
            when = f"resets at {at} (from the provider)" if self.known else f"retrying at about {at} (estimate)"
            return f"paused: {what}; {when}"
        return (
            f"paused: the model provider is rate limiting (not the usage quota); "
            f"retrying at about {at} (estimate, pause {self.throttles})"
        )


def plan_pause(limit: Limit, resets_at: int | None, now: datetime, throttles_before: int) -> PausePlan:
    """When a paused change wakes. A quota pause waits for the provider's reset (or an estimate when
    there is none); a throttle never borrows the quota window's reset, since that time is not when
    the throttle lifts (UX-18: the factory showed its own 30-minute fallback as the provider's)."""
    if limit.kind == "quota":
        if resets_at:
            at = datetime.fromtimestamp(resets_at, now.tzinfo)
            return PausePlan("quota", max(at, now), True, 0, limit.window, False)
        return PausePlan("quota", now + timedelta(minutes=QUOTA_FALLBACK_MINUTES), False, 0, limit.window, False)
    n = throttles_before + 1
    minutes = THROTTLE_MINUTES[min(n, len(THROTTLE_MINUTES)) - 1]
    return PausePlan("throttle", now + timedelta(minutes=minutes), False, n, None, n > MAX_THROTTLES)


# ------------------------------------------------------------------ meters --
DISAGREE_RATIO = 2.0  # one meter at least twice the other
DISAGREE_USD = 0.25  # and at least this far apart, so cents of rounding never alarm


def disagree(cli_usd: float, gateway_usd: float | None) -> str | None:
    """A note when the CLI's and the gateway's price of the same work are far apart, else None."""
    if gateway_usd is None:
        return None
    lo, hi = sorted((cli_usd, gateway_usd))
    if hi - lo < DISAGREE_USD or (lo > 0 and hi / lo < DISAGREE_RATIO):
        return None
    ratio = f"{hi / lo:.1f}×" if lo > 0 else "more than"
    higher = "gateway" if gateway_usd > cli_usd else "agent CLI"
    return (
        f"the two cost meters disagree: agent CLI ${cli_usd:.2f}, gateway ${gateway_usd:.2f} "
        f"(the {higher} {ratio} the other)"
    )


def usage_summary(model_usage: dict[str, dict[str, object]] | None) -> dict[str, dict[str, float]]:
    """Per-model tokens and the CLI's own price, kept so the two meters can be reconciled from
    evidence (L-5): a gateway that prices cache reads as fresh input shows up here as a gap that
    grows with `cache_read`."""
    out: dict[str, dict[str, float]] = {}
    for model, u in (model_usage or {}).items():
        if not isinstance(u, dict):
            continue

        def num(key: str, u: dict[str, object] = u) -> float:
            v = u.get(key)
            return float(v) if isinstance(v, int | float) else 0.0

        out[model] = {
            "input": num("inputTokens"),
            "output": num("outputTokens"),
            "cache_read": num("cacheReadInputTokens"),
            "cache_write": num("cacheCreationInputTokens"),
            "cost_usd": round(num("costUSD"), 4),
        }
    return out


# ----------------------------------------------------------------- budgets --
GATEWAY_BUDGET_TEXT = re.compile(r"budget has been exceeded|budget_exceeded|exceededbudget", re.IGNORECASE)


@dataclass(frozen=True)
class BudgetUse:
    """What one budget hold needs to say. Gateway figures are None when the call went direct."""

    scope: str  # call | change
    cap_usd: float
    call_cli_usd: float
    call_gateway_usd: float | None
    change_cli_usd: float
    change_gateway_usd: float | None
    role: str


def budget_hold(u: BudgetUse) -> str:
    """The hold, budget first (UX-15): which cap, where it is set, usage on both meters, what to do."""

    def meters(cli: float, gw: float | None) -> str:
        return f"agent CLI ${cli:.2f}" + (f", gateway ${gw:.2f}" if gw is not None else "")

    if u.scope == "call":
        head = (
            f"model budget per agent call reached: the {u.role} call stopped at the gateway's cap of "
            f"${u.cap_usd:.2f} (model_gateway.call_budget_usd, counted on the gateway's meter)"
        )
        todo = (
            "Resume gives this step a new call under the same cap, so a step this large may stop again; "
            "an admin can raise model_gateway.call_budget_usd in the factory config, or the request can "
            "be cut down. The factory never raises a cap itself."
        )
    else:
        head = (
            f"model budget for this change reached: ${u.cap_usd:.2f} (model_gateway.change_budget_usd, "
            "counted on the higher of the two meters)"
        )
        todo = (
            "Resume stops here again until the cap allows another call: an admin can raise "
            "model_gateway.change_budget_usd in the factory config, or the change can be cancelled and "
            "the request cut down. The factory never raises a cap itself."
        )
    lines = [
        head,
        f"this call: {meters(u.call_cli_usd, u.call_gateway_usd)}",
        f"this change so far: {meters(u.change_cli_usd, u.change_gateway_usd)}",
    ]
    if note := disagree(u.call_cli_usd, u.call_gateway_usd):
        lines.append(f"note: {note} for this call")
    lines.append(todo)
    return "\n".join(lines)
