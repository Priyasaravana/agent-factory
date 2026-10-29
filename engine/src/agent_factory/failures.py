"""Failure classes (ADR-0043): is it the product, the factory, or a person's call?

Every station failure used to take one route, `on_fail`, to a repair station, so an expired model
key or an overloaded model API reached the developer agent as if the code were wrong. This module
names the class of a failed step from what the step returned. It is pure: rules are data, each
with a test that also shows an ordinary work failure doesn't match (`tests/test_failures.py`).

- `work`: the product is wrong (tests, scans, review, e2e). Routed by `on_fail`, as before.
- `infra`: the factory or something it depends on failed, and the same step may pass if it is
  repeated. Retried in place with backoff (`retryable`), else held for a person.
- `decision`: only a person can unblock it (a budget, an accepted risk). Held with the action.

Anything no rule recognises is `work`, today's behaviour: the classifier can only take a wrong
route away, never add one. Commands the agents wrote (`untrusted`: `make verify`, `make e2e`) are
always `work`, even when they time out: a hung test is the product's fault, and their output is
the product's own text, which must not steer the route.
"""

from __future__ import annotations

import hashlib
import random
import re
import shlex
from dataclasses import dataclass
from enum import StrEnum


class FailureClass(StrEnum):
    work = "work"
    infra = "infra"
    decision = "decision"


@dataclass(frozen=True)
class Failure:
    cls: FailureClass
    rule: str  # the rule id that matched; "unrecognised" for work by default
    cause: str  # one line a person can read
    retryable: bool = False  # infra only: worth repeating the same step
    immediate: bool = False  # retry at once, without backoff (a fresh key fixes an expired one)

    def data(self) -> dict[str, object]:
        return {"class": str(self.cls), "rule": self.rule, "cause": self.cause, "retryable": self.retryable}


WORK = Failure(FailureClass.work, "unrecognised", "the step failed")


def host_asleep(seconds: float) -> Failure:
    """A command that failed while the factory host slept (the computer or Docker was suspended):
    nothing about the code is known yet, so it runs again at once instead of going to the developer."""
    return Failure(
        FailureClass.infra,
        "host_asleep",
        f"the factory host was asleep for {_hms(seconds)} during the command (the computer slept or Docker was paused)",
        retryable=True,
        immediate=True,
    )


def awake_note(seconds: float) -> str:
    """What the developer is told when a command cut off by host sleep fails again with the host awake."""
    return (
        f"\n[factory] An earlier run of this command was cut off: the factory host was asleep for {_hms(seconds)}."
        " The factory ran it again with the host awake and it failed again, as shown above: treat this as a"
        " real failure."
    )


def _hms(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: str  # regex, case-insensitive, searched in the text the rule reads
    cls: FailureClass
    cause: str
    retryable: bool = True
    immediate: bool = False
    error_only: bool = False  # agent rules: read the runner's error, never the agent's own words


# ------------------------------------------------------------- agent calls --
# Read from an agent call that did not succeed: the runner's error and the agent's last text
# (the CLI reports an API failure as the session's text, e.g. "Failed to authenticate. API Error:
# 401 ..."). Only consulted when the call itself failed, so an agent that merely *talks* about a
# 401 in the code it writes is never matched.
AGENT_RULES: tuple[Rule, ...] = (
    Rule(
        "model_budget",
        r"model budget for this change reached",
        FailureClass.decision,
        "the model budget for this change is used up",
        retryable=False,
    ),
    Rule(
        "gateway_setup",
        r"model gateway admin key not set",
        FailureClass.infra,
        "the model gateway is not set up (admin key missing)",
        retryable=False,
    ),
    Rule(
        "sandbox_setup",
        r"not installed; refusing to run unsandboxed",
        FailureClass.infra,
        "the agent sandbox is not installed",
        retryable=False,
    ),
    Rule(
        "key_expired",
        r"expired key|key expiry time",
        FailureClass.infra,
        "the model key expired during the call",
        immediate=True,
    ),
    Rule(
        "auth_failed",
        r"failed to authenticate|api error: 401|invalid (x-)?api[- ]key|authentication_error",
        FailureClass.infra,
        "the model API refused the key",
    ),
    Rule("gateway_down", r"model gateway (unreachable|refused a key)", FailureClass.infra, "the model gateway is down"),
    Rule(
        "model_overloaded",
        r"overloaded_error|api error: (529|5\d\d)",
        FailureClass.infra,
        "the model API is overloaded or failing",
    ),
    Rule(
        "rate_limited",
        r"api error: 429|rate_limit_error",
        FailureClass.infra,
        "the model API is rate limiting",
    ),
    Rule(
        "network",
        r"econnreset|econnrefused|etimedout|eai_again|socket hang up|connection (reset|refused|error|aborted)|"
        r"network (error|is unreachable)|request timed out|read timeout",
        FailureClass.infra,
        "the network to the model failed",
        error_only=True,
    ),
    Rule(
        "sandbox_not_ready",
        r"^agent sandbox (egress .*)?not ready",
        FailureClass.infra,
        "the agent sandbox is not ready",
        error_only=True,
    ),
    Rule("stalled", r"^agent call stalled", FailureClass.infra, "the agent call stopped responding", error_only=True),
    Rule(
        "call_crashed",
        r"^agent call did not finish$|^(ProcessError|CLIConnectionError|CLIJSONDecodeError|ClaudeSDKError)\b",
        FailureClass.infra,
        "the agent session crashed",
        error_only=True,
    ),
)


def classify_agent(error: str | None, text: str = "") -> Failure:
    """The class of an agent call that did not succeed. Unknown errors are `work`."""
    both = "\n".join(s for s in (error or "", (text or "")[-2000:]) if s)
    for r in AGENT_RULES:
        if re.search(r.pattern, (error or "") if r.error_only else both, re.IGNORECASE | re.MULTILINE):
            return Failure(r.cls, r.id, r.cause, r.retryable and r.cls == FailureClass.infra, r.immediate)
    return WORK


# ---------------------------------------------------------------- commands --
# Privileged commands the engine runs itself (docker, kind, helm, kubectl, git, gh). The rules
# read only the last part of the output, where the tool reports its own error.
COMMAND_TAIL = 1500
COMMAND_RULES: tuple[Rule, ...] = (
    Rule(
        "docker_daemon",
        r"cannot connect to the docker daemon|error during connect|docker daemon is not running|"
        r"is the docker daemon running",
        FailureClass.infra,
        "the Docker daemon is not reachable",
    ),
    Rule(
        "image_pull",
        r"errimagepull|imagepullbackoff|toomanyrequests|failed to resolve reference.*(timeout|eof|reset)|"
        r"error pulling image|net/http: TLS handshake timeout",
        FailureClass.infra,
        "an image pull failed",
    ),
    Rule(
        "cluster_unreachable",
        r"unable to connect to the server|the connection to the server .* was refused|"
        r"server is currently unable to handle the request|etcdserver: request timed out|"
        r"kubernetes cluster unreachable|no route to host",
        FailureClass.infra,
        "the cluster is not reachable",
    ),
    Rule(
        "network",
        r"i/o timeout|connection reset by peer|dial tcp .*(refused|timeout)|eai_again|temporary failure in name "
        r"resolution|could not resolve host|unexpected eof|502 bad gateway|503 service unavailable|"
        r"504 gateway timeout|econnreset|etimedout|network timeout",
        FailureClass.infra,
        "a network call failed",
    ),
)

# A tool run in a container (`docker run`, a scanner) reports on the product, so its output is
# read only for failures to get going: Docker's own, pulling the tool's image, and a scanner's
# database download (live run 2026-10-22: Trivy's DB download was cut off while the Mac slept,
# "unexpected EOF", and the build went to the developer as a code failure). Generic network words
# stay work here: a scanner's report can contain them.
SCANNER_DB = Rule(
    "scanner_db",
    r"failed to download (the )?vulnerability db|db error: failed to download|"
    r"failed to (download|update) (the )?(vulnerability )?database",
    FailureClass.infra,
    "the scanner could not download its vulnerability database",
)
CONTAINER_RULES: tuple[Rule, ...] = (
    COMMAND_RULES[0],  # docker_daemon
    COMMAND_RULES[1],  # image_pull
    SCANNER_DB,
)

# tools whose own errors are about the factory, not the product
INFRA_TOOLS = frozenset({"docker", "kind", "kubectl", "helm", "git", "gh", "skopeo", "crane", "oras"})
DOCKER_DAEMON_EXIT = 125  # `docker run`: the daemon failed, the container never ran


def _tools(command: str) -> list[str]:
    """The program of each step of a shell command line (`set -e; helm ... && kubectl ...`)."""
    out = []
    for seg in re.split(r"&&|\|\||;|\||\bdo\b|\bthen\b", command):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        for w in words:  # skip env assignments and wrappers
            if (
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)
                or w in {"set", "sudo", "exec", "env", "timeout"}
                or w[:1] == "-"
            ):
                continue
            out.append(w.rsplit("/", 1)[-1])
            break
    return out


def classify_command(command: str, returncode: int, output: str, timed_out: bool, *, untrusted: bool) -> Failure:
    """The class of a command that did not succeed. Untrusted commands and unknown errors are `work`."""
    if untrusted:
        return WORK
    tools = [t for t in _tools(command) if t in INFRA_TOOLS]
    if not tools:
        return WORK  # e.g. a health-check loop with curl: the app not answering is the product's
    if timed_out:
        return Failure(FailureClass.infra, "command_timeout", f"`{tools[0]}` timed out", True)
    tail = output[-COMMAND_TAIL:]
    if re.search(r"\bdocker\s+run\b", command):
        # a tool run in a container (a scanner): its output is about the product, its non-zero
        # exits are findings; only failures to get the tool going are the factory's
        if returncode == DOCKER_DAEMON_EXIT:
            return Failure(FailureClass.infra, "docker_run_daemon", "Docker could not start the container", True)
        for r in CONTAINER_RULES:
            if re.search(r.pattern, tail, re.IGNORECASE):
                return Failure(r.cls, r.id, r.cause, True)
        return WORK
    for r in COMMAND_RULES:
        if re.search(r.pattern, tail, re.IGNORECASE):
            return Failure(r.cls, r.id, r.cause, r.retryable and r.cls == FailureClass.infra, r.immediate)
    return WORK


# ------------------------------------------------------------- no progress --
# The no-progress stop (ADR-0043 follow-up). Book lending (2026-10-25): live e2e failed three times
# with `getaddrinfo ENOTFOUND dind`; the developer twice changed nothing ("the failure is an
# environment problem"), the same failure came back each time, and the loop ran until build's
# attempt budget held it (~$4.90). Two failures are "the same" when their evidence matches once
# what changes from run to run (timings, ports, ids, colours) is taken out.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HEXISH = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b")  # commit and image ids, digests
_NUMBER = re.compile(r"\d+(\.\d+)?")


def failure_fingerprint(evidence: str) -> str:
    """A short stable id of a failure's evidence: equal for the same failure on a later run.
    Pure. Timings, counts, ports, line numbers and ids are masked, so a fingerprint is coarse on
    purpose: it only ever stops a loop in which the repair also changed nothing."""
    text = _ANSI.sub("", evidence or "").lower()
    text = _HEXISH.sub("#", text)
    text = _NUMBER.sub("0", text)
    text = " ".join(text.split())
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ----------------------------------------------------------------- backoff --
def backoff(attempt: int, schedule: list[float], rnd: random.Random | None = None) -> float:
    """Seconds to wait before retry `attempt` (1-based): full jitter over the schedule's step,
    so retries from several changes don't arrive together. The schedule is the cap."""
    if not schedule:
        return 0.0
    ceiling = schedule[min(attempt, len(schedule)) - 1]
    return (rnd or random).uniform(ceiling / 2, ceiling)
