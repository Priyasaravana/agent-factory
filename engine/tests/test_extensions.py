"""The extension seam (ADR-0051): how the licensed edition plugs into the core, and how the core stays whole
without it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import PRODUCT, wait_run
from test_access import MEMBER
from test_api import _client

from agent_factory import extensions, signing
from agent_factory.models import Change, ChangeStatus
from agent_factory.state.sqlite import SqliteStateStore


@pytest.fixture(autouse=True)
def _core_registry():
    """Every test starts and ends on the core edition: the registry is process-wide."""
    extensions.activate(extensions.Registry())
    yield
    extensions.activate(extensions.Registry())


class _EP:
    """Stands in for an importlib.metadata entry point."""

    def __init__(self, name: str, obj=None, error: Exception | None = None):
        self.name, self._obj, self._error = name, obj, error

    def load(self):
        if self._error:
            raise self._error
        return self._obj


class _Ext:
    def __init__(self, name: str, fn):
        self.name, self._fn = name, fn

    def register(self, reg: extensions.Registry) -> None:
        self._fn(reg)


class _Sink:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


# --------------------------------------------------------------------- loading --
def test_the_core_finds_no_extensions_and_runs_as_core():
    reg = extensions.load([])
    assert reg.edition == "core" and reg.loaded == [] and reg.signer(Path("/x")) is None
    reg.audit("change.status", change_id="c1")  # no sinks: a no-op, never an error


def test_an_extension_registers_what_it_provides_and_the_core_records_it():
    sink = _Sink()

    def register(reg):
        reg.set_edition("enterprise", {"licensee": "Acme", "features": ["siem"]})
        reg.provide("audit-file", "siem", audit_sink=sink, ui_slot="settings.edition")

    reg = extensions.load([_EP("licensed", _Ext("licensed", register))])
    assert reg.edition == "enterprise" and reg.licence == {"licensee": "Acme", "features": ["siem"]}
    assert [(e.name, e.feature, e.status, e.points) for e in reg.loaded] == [
        ("audit-file", "siem", "loaded", ["audit_sink", "ui_slot"])
    ]
    assert reg.ui_slots == {"settings.edition": "audit-file"}
    reg.audit("change.status", change_id="c1", to_status="running")
    assert sink.events[0]["kind"] == "change.status" and sink.events[0]["change_id"] == "c1"
    assert "at" in sink.events[0]


def test_a_refusing_or_broken_extension_is_recorded_and_the_factory_starts_on_core():
    def refuse(_reg):
        raise extensions.Refused("no licence key")

    reg = extensions.load(
        [
            _EP("licensed", _Ext("licensed", refuse)),
            _EP("broken", error=ImportError("missing module")),
        ]
    )
    assert reg.edition == "core"
    assert [(e.name, e.status) for e in reg.loaded] == [("licensed", "refused"), ("broken", "failed")]
    assert reg.loaded[0].reason == "no licence key" and "missing module" in reg.loaded[1].reason


def test_a_single_point_is_provided_once_and_an_unknown_point_refuses_the_whole_feature():
    reg = extensions.Registry()
    first = reg.provide("kms-a", "kms", evidence_signer=lambda d: signing.LocalSigner(d))
    second = reg.provide("kms-b", "kms", evidence_signer=lambda d: signing.LocalSigner(d))
    odd = reg.provide("odd", "x", audit_sink=_Sink(), turn_off_sandbox=True)
    assert first.status == "loaded"
    assert second.status == "refused" and "already provided: evidence_signer by kms-a" in second.reason
    assert odd.status == "refused" and "turn_off_sandbox" in odd.reason
    assert reg.audit_sinks == [], "all or nothing: the odd feature's sink was not kept"


def test_a_broken_audit_sink_never_stops_the_others_or_the_caller():
    class Boom:
        def emit(self, event):
            raise RuntimeError("SIEM down")

    good = _Sink()
    reg = extensions.Registry()
    reg.provide("bad", "siem", audit_sink=Boom())
    reg.provide("good", "siem", audit_sink=good)
    reg.audit("change.status", change_id="c1")
    assert len(good.events) == 1


def test_safety_is_not_an_extension_point():
    """The floor, sandbox, sealing, signing checks, verifier and sign-in stay in core (ADR-0051)."""
    for point in extensions.POINTS:
        assert not any(w in point for w in ("floor", "sandbox", "seal", "verify", "mfa", "policy", "hook"))


# ------------------------------------------------------------- core call sites --
def test_an_extensions_signer_signs_evidence_and_the_core_still_checks_it(tmp_path):
    kms_dir = tmp_path / "kms"
    reg = extensions.activate(extensions.Registry())
    reg.provide("kms", "kms", evidence_signer=lambda _d: signing.LocalSigner(kms_dir))
    s = signing.signer_for(tmp_path / "data")
    assert isinstance(s, signing.LocalSigner) and s.dir == kms_dir / "keys" / "evidence"
    msg = b'{"manifest": 1}'
    assert signing.check(msg, s.sign(msg), s.trusted()).ok
    assert not (tmp_path / "data" / "keys").exists(), "the local key was never made"


def test_without_an_extension_evidence_is_signed_with_the_local_key(tmp_path):
    s = signing.signer_for(tmp_path)
    assert isinstance(s, signing.LocalSigner) and s.dir == tmp_path / "keys" / "evidence"


def test_every_change_created_and_every_status_change_reaches_the_audit_sinks():
    sink = _Sink()
    extensions.activate(extensions.Registry()).provide("audit", "siem", audit_sink=sink)
    store = SqliteStateStore(":memory:")
    now = datetime.now(UTC)
    ch = store.create_change(
        Change(
            id="c1",
            product_id="p1",
            iteration=1,
            status=ChangeStatus.queued,
            requested_by="ana",
            created_at=now,
            updated_at=now,
        )
    )
    ch.status = ChangeStatus.running
    store.save_change(ch)
    store.save_change(ch)  # no move, no event
    assert [(e["kind"], e.get("from_status"), e["to_status"]) for e in sink.events] == [
        ("change.created", None, "queued"),
        ("change.status", "queued", "running"),
    ]
    assert all(e["change_id"] == "c1" and e["product_id"] == "p1" and e["requested_by"] == "ana" for e in sink.events)


async def test_the_api_says_core_when_nothing_is_installed(make_factory):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        r = (await c.get("/api/edition", headers=MEMBER)).json()
        assert r == {"edition": "core", "licence": None, "extensions": [], "ui_slots": {}}
        # and the journey still runs on core: no extension is needed to deliver
        product = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()["product"]
        assert await wait_run(f, product["latest_change_id"]) == ChangeStatus.awaiting_feedback
