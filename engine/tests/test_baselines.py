"""Phase A item 2 (vision G0): the baselines, recorded once from the live runs, in docs/outcomes.md."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from agent_factory import baselines
from agent_factory.models import DurationStat, HumanTouches, OutcomesView, TimeSplit

REPO = Path(__file__).resolve().parents[2]
TAKEN = datetime(2026, 11, 25, 21, 30, tzinfo=UTC)


def _view(**kw) -> OutcomesView:  # noqa: ANN003
    base = dict(
        window_days=365, since=datetime(2025, 11, 25, tzinfo=UTC), generated_at=TAKEN, deliveries=8,
        deliveries_per_week=0.2, lead_time=DurationStat(median_s=2700, p90_s=4 * 3600, n=8), change_failure_rate=0.3,
        finished=10, failed_or_rescued=3, recovery_time=DurationStat(median_s=900, p90_s=1800, n=2),
        autonomy_ratio=0.625, touches=HumanTouches(), unplanned_touches_per_delivery=0.5, cost_total_usd=52.0,
        cost_per_delivery_usd=6.5, gateway_cost_total_usd=55.2, gateway_cost_per_delivery_usd=6.9,
        fix_loops_per_delivery=1.25, time_split=TimeSplit(), products=4, products_level3=4, requirements_total=30,
        requirements_verified_live=29,
    )  # fmt: skip
    return OutcomesView(**{**base, **kw})


def test_the_section_states_each_measure_its_source_and_its_target():
    text = baselines.section(_view(), install="reference install (Mac)", version="b688da3", taken=TAKEN)
    assert text.startswith(baselines.BEGIN) and text.rstrip().endswith(baselines.END)
    assert "Recorded 2026-11-25 21:30 UTC on reference install (Mac) (factory b688da3), over the last 365 days" in text
    rows = {
        line.split(" | ")[0].lstrip("| "): line
        for line in text.splitlines()
        if line.startswith("| ") and "---" not in line
    }
    assert (
        rows["Autonomy (no unplanned human touch)"]
        == "| Autonomy (no unplanned human touch) | 62% | 8 deliveries | ≥ 80% |"
    )
    assert rows["Change failure rate"].endswith("| 30% | 10 finished, 3 failed or rescued | ≤ 15% |")
    assert "| p50 45 min, p90 4.0 h | 8 deliveries |" in rows["Lead time"]
    assert (
        "| $6.50 (gateway: $6.90) | $52.00 over 8 deliveries | ≤ $10, both meters agreeing |"
        in rows["Cost per delivered change, failures included"]
    )
    assert "| 97% | 29 of 30," in rows["Requirements verified live"]
    assert "| 100% | 4 of 4 live products |" in rows["Share of live products at Level 3"]
    assert "1.2 fix loops per delivery" in text or "1.3 fix loops per delivery" in text


def test_nothing_to_measure_says_so_instead_of_inventing_zeroes():
    empty = _view(
        deliveries=0, lead_time=DurationStat(), change_failure_rate=None, finished=0, failed_or_rescued=0,
        autonomy_ratio=None, cost_total_usd=0.0, cost_per_delivery_usd=None, gateway_cost_total_usd=0.0,
        gateway_cost_per_delivery_usd=None, products=0, products_level3=0, requirements_total=0,
        requirements_verified_live=0,
    )  # fmt: skip
    text = baselines.section(empty, install="x", version="y", taken=TAKEN)
    assert "| Autonomy (no unplanned human touch) | n/a |" in text and "| n/a (no gateway meter) |" in text
    values = [line.split(" | ")[1] for line in text.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert all("n/a" in v and "%" not in v for v in values), values


def test_writing_replaces_only_the_section_and_twice_is_the_same():
    doc = (REPO / "docs" / "outcomes.md").read_text()
    assert baselines.BEGIN in doc and "Not recorded yet" in doc, "the placeholder ships until make baselines runs"
    text = baselines.section(_view(), install="i", version="v", taken=TAKEN)
    once = baselines.write_into(doc, text)
    assert "Not recorded yet" not in once and once.count(baselines.BEGIN) == 1
    assert once.replace(text.rstrip("\n"), "") == doc.replace(
        doc[doc.index(baselines.BEGIN) : doc.index(baselines.END) + len(baselines.END)], ""
    ), "nothing else in the page changes"
    assert baselines.write_into(once, text) == once
    bare = "# Outcomes\n\n## Using it\nread it\n"
    assert baselines.write_into(bare, text).index(baselines.BEGIN) < baselines.write_into(bare, text).index(
        "## Using it"
    )


def test_the_command_reads_the_store_and_writes_the_page(tmp_path, monkeypatch, capsys):
    from agent_factory import cli

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO((REPO / "docs" / "outcomes.md").read_text()))
    cli._baselines(365, "test install", "abc1234", "-")
    out = capsys.readouterr().out
    assert "on test install (factory abc1234)" in out and "Not recorded yet" not in out
    assert out.count(baselines.BEGIN) == 1 and "## Using it" in out


def test_make_baselines_runs_it_in_the_factory_and_only_replaces_the_page_when_it_worked():
    make = (REPO / "Makefile").read_text()
    body = make.split("\nbaselines:", 1)[1].split("\n\n", 1)[0]
    assert "agent-factory baselines --into -" in body and "< docs/outcomes.md > .cache/outcomes.md" in body
    assert body.index("agent-factory baselines") < body.index("mv .cache/outcomes.md docs/outcomes.md")
