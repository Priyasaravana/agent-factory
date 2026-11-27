"""Helm release operations shared by the deploy providers that install with Helm (`local`, `helm`):
what a deploy installed, and a fail-closed rollback to a recorded release (ADR-0046 slice 1).

The provider supplies how a command reaches its cluster (`run`: a kubeconfig, a context) and how
the app is reached (`probe`: curl, through the ingress or the node port). Decisions are the pure
functions in `agent_factory.rollback`.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_factory import blue_green, respond, rollback
from agent_factory.executor import CommandResult, OutputCutOff
from agent_factory.models import Release
from agent_factory.providers.base import StepResult

Run = Callable[[str, float], Awaitable[CommandResult]]  # (command, timeout) -> result, on the release's cluster
Probe = Callable[[str], Awaitable[CommandResult]]  # (path) -> the app answered it, retried for a while

# Helm's own answer when the release doesn't exist, and nothing else: a missing kube context, namespace
# or chart also says "not found", and taking those for "nothing installed" would fail open (#150)
_NO_RELEASE = re.compile(r"^Error: release: not found\s*$", re.MULTILINE)


def _whole(res: CommandResult) -> str:
    """The complete output, or "" when it was cut off: every parser it feeds reads "" as unknown
    (no revision, no history, no image, no pods) and the caller fails closed on that."""
    try:
        return res.whole()
    except OutputCutOff:
        return ""


# per pod: its deletion timestamp (empty unless terminating), then each container as image=imageID,
# so the release's own containers can be told from sidecars (`rollback.app_digests`)
_POD = "{.metadata.deletionTimestamp}|"
_CONTAINERS = "{range .status.containerStatuses[*]}{.image}={.imageID} {end}"
PODS_JSONPATH = "{range .items[*]}" + _POD + _CONTAINERS + "{'\\n'}{end}"


@dataclass
class HelmRelease:
    run: Run
    helm: str  # `helm` with the provider's context flags
    kubectl: str  # `kubectl` with the provider's context flags
    name: str  # the Helm release
    namespace: str

    def _ns(self) -> str:
        return f"--namespace {shlex.quote(self.namespace)}"

    def _pods(self, color: str | None) -> str:
        """The release's pods; on a blue/green release only one colour's (ADR-0046 slice 4), since
        both colours run the same repository and only the tag tells them apart."""
        selector = self._selector(color)
        jsonpath = shlex.quote(PODS_JSONPATH)
        return f"{self.kubectl} get pods {self._ns()} -l {shlex.quote(selector)} -o jsonpath={jsonpath}"

    async def digest(self, image: str, color: str | None = None) -> tuple[str | None, str]:
        """The one digest every running container of the release's image reports (sidecars aside);
        `color`: of that colour's pods only."""
        res = await self.run(self._pods(color), 60)
        if not res.ok:
            return None, f"could not read the running pods: {res.output.strip()[-300:]}"
        try:
            return rollback.one_digest(res.whole(), image)
        except OutputCutOff as cut:
            return None, str(cut)

    async def installed(self, image: str, color: str | None = None) -> tuple[int | None, str | None, str]:
        """After `helm upgrade --wait`: (revision, digest, problem). Both are needed to record the
        release; a deploy that can't tell them fails closed."""
        # Helm includes the chart and rendered manifests in status JSON. Reduce it before
        # the executor keeps only its output tail, or large charts lose their revision.
        extract = (
            "import json, subprocess, sys; "
            "doc = json.loads(subprocess.check_output(sys.argv[1:], text=True)); "
            "print(json.dumps({'version': doc.get('version')}))"
        )
        command = f"{self.helm} status {shlex.quote(self.name)} {self._ns()} -o json"
        status = await self.run(f"python3 -c {shlex.quote(extract)} {command}", 60)
        revision = rollback.helm_revision(_whole(status)) if status.ok else None
        if revision is None:
            return None, None, "Helm reported no revision for the release"
        digest, why = await self.digest(image, color)
        return revision, digest, why

    async def values(self) -> tuple[str | None, str]:
        """`helm get values -o json` of the release: (output, problem). (None, "") only when Helm says
        it has no such release (nothing is installed yet); any other failure is a problem."""
        res = await self.run(f"{self.helm} get values {shlex.quote(self.name)} {self._ns()} -o json", 60)
        if res.ok:
            try:
                return res.whole(), ""
            except OutputCutOff as cut:
                return None, str(cut)
        if _NO_RELEASE.search(_whole(res)):
            return None, ""
        return None, f"could not read the release's values: {res.output.strip()[-300:]}"

    async def _history(self) -> list[dict[str, object]]:
        res = await self.run(f"{self.helm} history {shlex.quote(self.name)} {self._ns()} -o json", 60)
        return rollback.history(_whole(res)) if res.ok else []

    async def _image(self, revision: int | None = None) -> str | None:
        rev = f" --revision {revision}" if revision else ""
        res = await self.run(f"{self.helm} get values {shlex.quote(self.name)} {self._ns()}{rev} -o json", 60)
        return rollback.values_image(_whole(res)) if res.ok else None

    def _selector(self, color: str | None) -> str:
        return f"app.kubernetes.io/instance={self.name}" + (f",{blue_green.COLOR_LABEL}={color}" if color else "")

    async def state(self, image: str, color: str | None = None) -> respond.Live:
        """What the release runs now, read-only (incident response, ADR-0049 §4): Helm's newest revision and
        the image its values name, the digests the running containers report, the replicas each workload asks
        for against what the release's manifest says, and the pods' readiness. Anything that can't be read
        makes the whole state unreadable (`error`), so nothing is decided from half of it."""
        rows = await self._history()
        if not rows:
            return respond.Live(error="Helm reported no history for the release")
        newest = max(rows, key=lambda r: int(str(r["revision"])))
        live_image = await self._image()
        sel = shlex.quote(self._selector(color))
        pods = await self.run(self._pods(color), 60)
        health = await self.run(
            f"{self.kubectl} get pods {self._ns()} -l {sel} -o jsonpath={shlex.quote(respond.POD_HEALTH_JSONPATH)}", 60
        )
        loads = await self.run(
            f"{self.kubectl} get deployments,statefulsets {self._ns()} -l {shlex.quote(self._selector(None))} "
            f"-o jsonpath={shlex.quote(respond.WORKLOADS_JSONPATH)}",
            60,
        )
        manifest = await self.run(f"{self.helm} get manifest {shlex.quote(self.name)} {self._ns()}", 60)
        unread = [what for what, res in (("pods", pods), ("pod health", health), ("workloads", loads),
                                         ("manifest", manifest)) if not res.ok]  # fmt: skip
        if unread:
            return respond.Live(error="could not read the release's " + ", ".join(unread))
        n, not_ready, crashing, restarts = respond.pod_health(_whole(health))
        expected = respond.manifest_replicas(_whole(manifest))
        if color:  # blue/green: only the live colour's Deployment is compared (green's name ends in -green)
            expected = {
                k: v for k, v in expected.items()
                if not k.startswith("Deployment/") or k.endswith("-green") == (color == "green")
            }  # fmt: skip
        return respond.Live(
            revision=int(str(newest["revision"])),
            description=str(newest["description"]),
            status=str(newest["status"]),
            image=live_image,
            digests=rollback.app_digests(_whole(pods), image),
            workloads=respond.workloads(_whole(loads)),
            expected=expected,
            pods=n,
            not_ready=not_ready,
            crashing=crashing,
            restarts=restarts,
        )

    async def restart(self, color: str | None, probe: Probe, paths: list[str]) -> StepResult:
        """Restart the release's Deployments (never a StatefulSet: that is where the data lives) and wait for
        the rollout, then the health paths must answer (incident response, the ladder's second step)."""
        sel = shlex.quote(self._selector(color))
        res = await self.run(f"{self.kubectl} rollout restart deployment {self._ns()} -l {sel}", 120)
        if not res.ok:
            return StepResult(False, "the restart was refused", res.output[-1500:])
        rollout = await self.run(f"{self.kubectl} rollout status deployment {self._ns()} -l {sel} --timeout=180s", 240)
        problems = [] if rollout.ok else [f"rollout did not complete: {rollout.output.strip()[-300:]}"]
        for path in paths:
            answered = await probe(path)
            if not answered.ok:
                problems.append(f"{path} did not answer: {answered.output.strip()[-300:]}")
        if problems:
            return StepResult(False, "restarted, but " + "; ".join(problems), data={"verify": problems})
        return StepResult(True, f"restarted; rollout complete, {', '.join(paths)} ok")

    async def rollback(self, release: Release, now: dict[str, str], probe: Probe, paths: list[str]) -> StepResult:
        """Restore `release`, failing closed: refuse unless the record is complete, names this
        target, and its revision is in the history with the recorded image; then roll back to that
        revision exactly and verify the result (history, image, rollout, digest, health paths)."""
        refuse = rollback.before_rollback(release, now)
        if not refuse:
            refuse = rollback.history_problems(release, await self._history(), await self._image(release.revision))
        if refuse:
            return StepResult(False, "rollback refused: " + "; ".join(refuse), data={"refused": refuse})
        cmd = f"{self.helm} rollback {shlex.quote(self.name)} {release.revision} {self._ns()} --wait --timeout 5m"
        res = await self.run(cmd, 420)
        if not res.ok:  # Helm's own reason in the summary too, so the card says why without opening the output
            tail = res.output[-2500:]  # shown, never decided on: Helm's last error line, for the card's summary
            why = next((ln.strip() for ln in reversed(tail.strip().splitlines()) if "rror" in ln), "")
            return StepResult(False, "helm rollback failed" + (f": {why[-300:]}" if why else ""), tail)
        rollout = await self.run(
            f"{self.kubectl} rollout status deployment {self._ns()} "
            f"-l app.kubernetes.io/instance={shlex.quote(self.name)} --timeout=180s",
            240,
        )
        problems = [] if rollout.ok else [f"rollout did not complete: {rollout.output.strip()[-300:]}"]
        rows = await self._history()
        latest = max(rows, key=lambda r: int(str(r["revision"]))) if rows else None
        pods = await self.run(self._pods(release.color), 60)  # blue/green: the colour restored to users
        problems += rollback.restored_problems(release, latest, await self._image(), _whole(pods) if pods.ok else "")
        for path in paths:
            answered = await probe(path)
            if not answered.ok:
                problems.append(f"{path} did not answer: {answered.output.strip()[-300:]}")
        if problems:
            return StepResult(
                False, "rolled back, but the restored release failed its checks: " + "; ".join(problems),
                data={"verify": problems},
            )  # fmt: skip
        return StepResult(
            True,
            f"rolled back to revision {release.revision} ({release.image}, {release.digest}); "
            f"rollout complete, {', '.join(paths)} ok",
        )
