"""Helm release operations shared by the deploy providers that install with Helm (`local`, `helm`):
what a deploy installed, and a fail-closed rollback to a recorded release (ADR-0046 slice 1).

The provider supplies how a command reaches its cluster (`run`: a kubeconfig, a context) and how
the app is reached (`probe`: curl, through the ingress or the node port). Decisions are the pure
functions in `agent_factory.rollback`.
"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_factory import blue_green, rollback
from agent_factory.executor import CommandResult
from agent_factory.models import Release
from agent_factory.providers.base import StepResult

Run = Callable[[str, float], Awaitable[CommandResult]]  # (command, timeout) -> result, on the release's cluster
Probe = Callable[[str], Awaitable[CommandResult]]  # (path) -> the app answered it, retried for a while

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
        selector = f"app.kubernetes.io/instance={self.name}" + (f",{blue_green.COLOR_LABEL}={color}" if color else "")
        jsonpath = shlex.quote(PODS_JSONPATH)
        return f"{self.kubectl} get pods {self._ns()} -l {shlex.quote(selector)} -o jsonpath={jsonpath}"

    async def digest(self, image: str, color: str | None = None) -> tuple[str | None, str]:
        """The one digest every running container of the release's image reports (sidecars aside);
        `color`: of that colour's pods only."""
        res = await self.run(self._pods(color), 60)
        if not res.ok:
            return None, f"could not read the running pods: {res.output.strip()[-300:]}"
        return rollback.one_digest(res.output, image)

    async def installed(self, image: str, color: str | None = None) -> tuple[int | None, str | None, str]:
        """After `helm upgrade --wait`: (revision, digest, problem). Both are needed to record the
        release; a deploy that can't tell them fails closed."""
        status = await self.run(f"{self.helm} status {shlex.quote(self.name)} {self._ns()} -o json", 60)
        revision = rollback.helm_revision(status.output) if status.ok else None
        if revision is None:
            return None, None, "Helm reported no revision for the release"
        digest, why = await self.digest(image, color)
        return revision, digest, why

    async def values(self) -> tuple[str | None, str]:
        """`helm get values -o json` of the release: (output, problem). (None, "") when Helm has no
        such release: nothing is installed yet."""
        res = await self.run(f"{self.helm} get values {shlex.quote(self.name)} {self._ns()} -o json", 60)
        if res.ok:
            return res.output, ""
        if "not found" in res.output.lower():
            return None, ""
        return None, f"could not read the release's values: {res.output.strip()[-300:]}"

    async def _history(self) -> list[dict[str, object]]:
        res = await self.run(f"{self.helm} history {shlex.quote(self.name)} {self._ns()} -o json", 60)
        return rollback.history(res.output) if res.ok else []

    async def _image(self, revision: int | None = None) -> str | None:
        rev = f" --revision {revision}" if revision else ""
        res = await self.run(f"{self.helm} get values {shlex.quote(self.name)} {self._ns()}{rev} -o json", 60)
        return rollback.values_image(res.output) if res.ok else None

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
        if not res.ok:
            return StepResult(False, "helm rollback failed", res.output[-2500:])
        rollout = await self.run(
            f"{self.kubectl} rollout status deployment {self._ns()} "
            f"-l app.kubernetes.io/instance={shlex.quote(self.name)} --timeout=180s",
            240,
        )
        problems = [] if rollout.ok else [f"rollout did not complete: {rollout.output.strip()[-300:]}"]
        rows = await self._history()
        latest = max(rows, key=lambda r: int(str(r["revision"]))) if rows else None
        pods = await self.run(self._pods(release.color), 60)  # blue/green: the colour restored to users
        problems += rollback.restored_problems(release, latest, await self._image(), pods.output if pods.ok else "")
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
