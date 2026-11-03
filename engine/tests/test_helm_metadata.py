"""Release identity survives large Helm status responses and still fails closed."""

import shlex
import sys

import pytest

from agent_factory.executor import CommandResult, LocalExecutor
from agent_factory.providers.helm_release import HelmRelease


@pytest.mark.parametrize("mode", ["large", "failed", "malformed", "missing"])
async def test_installed_status_is_compacted_before_executor_truncation(tmp_path, mode):
    helm = tmp_path / "helm.py"
    helm.write_text(
        "import json, sys\n"
        + {
            "large": "print(json.dumps({'version': 7, 'manifest': 'x' * 24000}))\n",
            "failed": "print(json.dumps({'version': 7})); sys.exit(2)\n",
            "malformed": "print('not json')\n",
            "missing": "print(json.dumps({'manifest': 'x' * 24000}))\n",
        }[mode]
    )
    executor = LocalExecutor()

    async def run(command, timeout):
        if command.startswith("kubectl "):
            return CommandResult(command, 0, "|app:v1=x@sha256:" + "a" * 64)
        return await executor.run(command, timeout=timeout)

    release = HelmRelease(run, f"{shlex.quote(sys.executable)} {shlex.quote(str(helm))}", "kubectl", "app", "ns")
    revision, digest, problem = await release.installed("app:v1")
    if mode == "large":
        assert (revision, digest, problem) == (7, "sha256:" + "a" * 64, "")
    else:
        assert revision is None and digest is None and problem
