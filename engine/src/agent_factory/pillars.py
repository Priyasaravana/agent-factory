"""Quality pillars (ADR-0024): the factory's quality model for a running product.

Ten pillars: the Well-Architected pillars plus the ISO/IEC 25010 qualities that
Well-Architected underplays. Every readiness signal carries exactly one primary
pillar (`readiness.Signal.pillar`). Pillars are a *view* on the same signals: the
Level 3 gate is unchanged. A pillar with no signals is reported as uncovered,
never as 100%.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Pillar:
    id: str
    title: str
    question: str


PILLARS: list[Pillar] = [
    Pillar("security", "Security", "Is it protected, least-privilege, auditable, with a trusted supply chain?"),
    Pillar(
        "reliability",
        "Reliability",
        "Does it keep working, or degrade gracefully, when parts fail? Can it be restored?",
    ),
    Pillar(
        "performance", "Performance & scalability", "Does it meet latency and throughput needs, and scale with load?"
    ),
    Pillar("operability", "Operability & observability", "Can operators see, deploy, diagnose and recover it?"),
    Pillar("cost", "Cost", "Is the cost to build and run visible and justified?"),
    Pillar("interoperability", "Interoperability", "Does it integrate through documented, standard interfaces?"),
    Pillar("usability", "Usability", "Can its users learn and use it, including users with accessibility needs?"),
    Pillar("maintainability", "Maintainability", "Is it cheap and safe to change?"),
    Pillar("portability", "Portability", "Can it be installed and moved across environments?"),
    Pillar(
        "compliance",
        "Compliance & evidence",
        "Can we prove what was built, from what, and that it meets its requirements?",
    ),
]
IDS = [p.id for p in PILLARS]
BY_ID = {p.id: p for p in PILLARS}


def tally(signals: Iterable[dict[str, Any]], pillar_of: dict[str, str]) -> dict[str, dict[str, int]]:
    """Passed and applicable signals per pillar, for every pillar (uncovered ones at 0/0).

    `signals` are scorecard entries ({id, ok, pillar?}); a missing pillar (scorecards
    from before ADR-0024) is looked up by signal id; unknown signals are skipped."""
    out = {p: {"passed": 0, "applicable": 0} for p in IDS}
    for s in signals:
        p = s.get("pillar") or pillar_of.get(str(s.get("id")))
        if p in out:
            out[p]["applicable"] += 1
            out[p]["passed"] += 1 if s.get("ok") else 0
    return out


# Evidence files (ADR-0023) by primary pillar, for the sealed pillar index. First match wins.
EVIDENCE_RULES: list[tuple[str, str]] = [
    ("sbom.spdx.json", "security"),
    ("provenance.json", "compliance"),
    ("traceability.json", "compliance"),
    ("review.json", "compliance"),
    ("readiness.json", "compliance"),
    ("spec/", "compliance"),
    ("events.jsonl", "operability"),
    ("sessions/", "operability"),
]


def evidence_pillar(path: str) -> str | None:
    for prefix, pillar in EVIDENCE_RULES:
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return pillar
    return None
