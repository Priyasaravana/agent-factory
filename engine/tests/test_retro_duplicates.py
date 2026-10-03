"""The retro doesn't suggest the same idea twice (ADR-0021): three runs produced three
rewordings of one verifier lesson while the first was still waiting for a person."""

from __future__ import annotations

from agent_factory.retro import _duplicate

WAITING = [
    "Send each probe as one standalone curl command with the full literal URL; do not use shell variables, "
    "for-loops, $(...) substitution or pipes like cut, since the observe-only guardrail rejects them. "
    "Repeat a call by issuing it again.",
]
REWORDED = [
    "Use the observe-only shell for plain single commands only: one literal curl per Bash call with the full URL, "
    "and no variable assignments, loops, pipes to grep/head, or bracket patterns.",
    "Because you are observe-only, probe the live app with one plain curl command per call, using literal URLs and "
    "inline JSON bodies; do not use shell variables, for-loops, jq filters or redirects to files.",
]


def test_a_reworded_lesson_counts_as_the_same_idea() -> None:
    for lesson in REWORDED:
        assert _duplicate(lesson, WAITING), lesson


def test_a_different_lesson_is_kept() -> None:
    other = "Read docs/openapi.yaml before writing routes and keep response models identical to the contract."
    assert not _duplicate(other, WAITING)
    assert not _duplicate("Validate query parameters with Pydantic and return 422 on bad input.", WAITING)
