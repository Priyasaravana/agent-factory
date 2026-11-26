# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""Phase C's "Done when", in process: the enterprise edition loads a feature with a valid key and refuses
it with an invalid one, through the core's real entry-point discovery."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from importlib import metadata

import pytest
from agent_factory import extensions
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agent_factory_licensed as edition
from agent_factory_licensed import licence, siem


@pytest.fixture
def issuer():
    return Ed25519PrivateKey.generate()


def _ext(issuer, key: str | None) -> edition.Enterprise:
    return edition.Enterprise(lambda: licence.check(key, [licence._raw_public(issuer)]))


def _valid(issuer, features=("siem",)) -> str:
    return licence.issue(issuer, "Acme Ltd", list(features), datetime.now(UTC).date() + timedelta(days=365))


class _EP:
    name = "licensed"

    def __init__(self, obj):
        self._obj = obj

    def load(self):
        return self._obj


def test_the_package_is_found_by_the_cores_entry_point_group():
    eps = [ep for ep in metadata.entry_points(group=extensions.GROUP) if ep.name == "licensed"]
    assert eps and eps[0].load() is edition.extension


def test_a_valid_key_loads_the_feature_and_audit_events_reach_the_file(issuer, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_FACTORY_AUDIT_DIR", str(tmp_path / "audit"))
    reg = extensions.load([_EP(_ext(issuer, _valid(issuer)))])
    assert reg.edition == "enterprise" and reg.licence and reg.licence["valid"]
    assert [(e.name, e.status) for e in reg.loaded] == [("audit-file", "loaded")]
    reg.audit("change.status", change_id="c1", to_status="running")
    reg.audit("change.status", change_id="c1", to_status="delivered")
    lines = [json.loads(ln) for f in sorted((tmp_path / "audit").glob("*.jsonl")) for ln in f.read_text().splitlines()]
    assert [ln["to_status"] for ln in lines] == ["running", "delivered"]
    assert siem.verify_chain(tmp_path / "audit")[0]


@pytest.mark.parametrize("key", [None, "AFL1.bad.key", "not a key"])
def test_an_invalid_or_missing_key_refuses_every_feature_and_core_runs_on(issuer, tmp_path, monkeypatch, key):
    monkeypatch.setenv("AGENT_FACTORY_AUDIT_DIR", str(tmp_path / "audit"))
    reg = extensions.load([_EP(_ext(issuer, key))])
    assert reg.edition == "enterprise" and reg.licence and not reg.licence["valid"]
    assert [(e.name, e.status) for e in reg.loaded] == [("siem", "refused")]
    assert reg.loaded[0].reason.startswith("licence: ")
    assert reg.audit_sinks == [] and reg.signer_factory is None
    reg.audit("change.status", change_id="c1")
    assert not (tmp_path / "audit").exists()


def test_a_valid_key_without_the_feature_refuses_only_that_feature(issuer):
    reg = extensions.load([_EP(_ext(issuer, _valid(issuer, features=("sso",))))])
    assert reg.licence and reg.licence["valid"]
    assert [(e.name, e.status, e.reason) for e in reg.loaded] == [
        ("siem", "refused", "the licence does not include this feature")
    ]


def test_an_expired_key_past_grace_refuses(issuer):
    key = licence.issue(issuer, "Acme", ["siem"], date(2020, 1, 1), issued=date(2019, 1, 1))
    reg = extensions.load([_EP(_ext(issuer, key))])
    assert [e.status for e in reg.loaded] == ["refused"] and "expired" in reg.loaded[0].reason


def test_the_audit_chain_shows_a_removed_or_edited_line(tmp_path):
    sink = siem.AuditFile(tmp_path)
    for i in range(3):
        sink.emit({"kind": "change.status", "n": i})
    f = next(tmp_path.glob("*.jsonl"))
    lines = f.read_text().splitlines()
    assert siem.verify_chain(tmp_path) == (True, "3 event(s), chain intact")
    f.write_text("\n".join([lines[0], lines[2]]) + "\n")
    ok, where = siem.verify_chain(tmp_path)
    assert not ok and ":2:" in where
    f.write_text("\n".join([lines[0], lines[1].replace('"n": 1', '"n": 9'), lines[2]]) + "\n")
    assert not siem.verify_chain(tmp_path)[0]


def test_the_chain_resumes_after_a_restart(tmp_path):
    siem.AuditFile(tmp_path).emit({"kind": "a"})
    siem.AuditFile(tmp_path).emit({"kind": "b"})  # a new process
    assert siem.verify_chain(tmp_path) == (True, "2 event(s), chain intact")
    f = next(tmp_path.glob("*.jsonl"))
    assert oct(f.stat().st_mode & 0o777) == "0o600"
