"""Signed evidence and the verifier anyone can run (ADR-0050, phase B).

The phase's "Done when", here: a sealed change's bundle verifies with only the factory's public key,
and every kind of tampering fails it (a file, the manifest re-hashed, a re-signed manifest, the
signature removed, a path that leaves the bundle)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import PRODUCT, wait_run
from test_access import MEMBER
from test_api import _client

from agent_factory import evidence, signing
from agent_factory.models import ChangeStatus

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify-evidence.py"
SSH_KEYGEN = shutil.which("ssh-keygen")
needs_ssh = pytest.mark.skipif(not SSH_KEYGEN, reason="OpenSSH ssh-keygen not installed")


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_evidence", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_evidence"] = mod
    spec.loader.exec_module(mod)
    return mod


V = _verifier()


# ------------------------------------------------------------------ signing --
def test_a_signature_checks_and_a_changed_message_does_not(tmp_path):
    s = signing.LocalSigner(tmp_path)
    sig = s.sign(b"manifest\n")
    c = signing.check(b"manifest\n", sig, s.trusted())
    assert c.ok and c.signer == signing.fingerprint(s.public_blob())
    bad = signing.check(b"manifest!\n", sig, s.trusted())
    assert bad.present and not bad.valid and not bad.ok and "does not match" in bad.reason


def test_a_valid_signature_by_another_key_is_not_trusted(tmp_path):
    ours, theirs = signing.LocalSigner(tmp_path / "a"), signing.LocalSigner(tmp_path / "b")
    c = signing.check(b"m", theirs.sign(b"m"), ours.trusted())
    assert c.valid and not c.trusted and not c.ok and "not a trusted key" in c.reason


def test_nothing_but_an_ssh_signature_in_our_namespace_is_accepted(tmp_path):
    s = signing.LocalSigner(tmp_path)
    assert not signing.check(b"m", None, s.trusted()).present
    for junk in ("", "hello", "-----BEGIN SSH SIGNATURE-----\n!!!\n-----END SSH SIGNATURE-----"):
        c = signing.check(b"m", junk, s.trusted())
        assert c.present and not c.ok
    blob = s.public_blob()
    raw = s._load_or_create().sign(signing.signed_data(b"m", "git"))
    other_ns = signing.armor(blob, raw, "git")
    c = signing.check(b"m", other_ns, s.trusted())
    assert not c.ok and "namespace" in c.reason, "a signature made for something else is not evidence"


def test_the_key_is_made_once_private_and_kept(tmp_path):
    s = signing.LocalSigner(tmp_path)
    assert s.trusted() == set(), "reading never makes a key"
    fp = signing.fingerprint(s.public_blob())
    assert stat.S_IMODE(s.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(s.dir.stat().st_mode) == 0o700
    again = signing.LocalSigner(tmp_path)
    assert signing.fingerprint(again.public_blob()) == fp
    assert s.pub_path.read_text().startswith("ssh-ed25519 ")


def test_two_seals_at_once_never_make_two_keys(tmp_path):
    a, b = signing.LocalSigner(tmp_path), signing.LocalSigner(tmp_path)
    a.dir.mkdir(parents=True, mode=0o700)
    ka = a._create()
    kb = b._create()  # the file exists: the second reads the first's key
    assert ka.public_key().public_bytes_raw() == kb.public_key().public_bytes_raw()


def test_rotating_keeps_earlier_evidence_verifiable(tmp_path):
    s = signing.LocalSigner(tmp_path)
    old_sig = s.sign(b"old")
    old, new = s.rotate()
    assert old and new and old != new
    assert signing.check(b"old", old_sig, s.trusted()).ok, "a rotated-out key is still trusted for what it signed"
    assert signing.check(b"new", s.sign(b"new"), s.trusted()).signer == new
    assert [signing.fingerprint(k) for k in s.retired()] == [old]


@needs_ssh
def test_ssh_keygen_and_we_agree_both_ways(tmp_path):
    s = signing.LocalSigner(tmp_path)
    msg = tmp_path / "manifest.json"
    msg.write_bytes(b'{"files": []}\n')
    (tmp_path / "sig").write_text(s.sign(msg.read_bytes()))
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(signing.allowed_signers_line(s.public_blob()) + "\n")
    cmd = [SSH_KEYGEN, "-Y", "verify", "-f", str(allowed), "-I", signing.IDENTITY, "-n", signing.NAMESPACE]
    res = subprocess.run([*cmd, "-s", str(tmp_path / "sig")], input=msg.read_bytes(), capture_output=True)  # noqa: S603
    assert res.returncode == 0, res.stderr
    fp = subprocess.run([SSH_KEYGEN, "-lf", str(s.pub_path)], capture_output=True, text=True).stdout  # noqa: S603
    assert signing.fingerprint(s.public_blob()) in fp
    # ssh-keygen signs, we check
    res = subprocess.run(  # noqa: S603
        [SSH_KEYGEN, "-q", "-Y", "sign", "-f", str(s.key_path), "-n", signing.NAMESPACE],
        input=msg.read_bytes(),
        capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    assert signing.check(msg.read_bytes(), res.stdout.decode(), s.trusted()).ok


# ------------------------------------------------------------ sealed folders --
def _seal(tmp_path: Path, signer: signing.LocalSigner | None = None) -> tuple[Path, str, str | None]:
    d = tmp_path / "artifacts" / "c1"
    (d / "sessions").mkdir(parents=True)
    (d / "events.jsonl").write_text('{"summary": "started"}\n')
    (d / "sessions" / "01-build.jsonl").write_text('{"type": "text"}\n')
    m = evidence.build_manifest(d, {"run": {"id": "c1"}}, datetime(2026, 11, 25, tzinfo=UTC))
    digest = evidence.write_manifest(d, m)
    by = evidence.sign_manifest(d, signer) if signer else None
    return d, digest, by


def test_a_signed_seal_is_intact_only_with_its_signature(tmp_path):
    s = signing.LocalSigner(tmp_path)
    d, digest, by = _seal(tmp_path, s)
    v = evidence.verify(d, digest, by, s.trusted())
    assert v.intact and v.signed and v.signature_ok and v.signer == by
    (d / evidence.SIGNATURE).unlink()
    v = evidence.verify(d, digest, by, s.trusted())
    assert not v.intact and v.signed and not v.signature_ok, "a removed signature is not a seal from before signing"


def test_a_seal_re_signed_by_another_key_is_not_intact(tmp_path):
    s, intruder = signing.LocalSigner(tmp_path / "f"), signing.LocalSigner(tmp_path / "x")
    d, digest, by = _seal(tmp_path, s)
    evidence.sign_manifest(d, intruder)
    v = evidence.verify(d, digest, by, s.trusted() | intruder.trusted())
    assert not v.intact and not v.signature_ok and v.signer != by


def test_a_seal_from_before_signing_is_checked_by_its_hashes_and_says_so(tmp_path):
    d, digest, _ = _seal(tmp_path)
    v = evidence.verify(d, digest)
    assert v.intact and not v.signed and not v.signature_ok


def test_the_signature_is_not_one_of_the_hashed_files(tmp_path):
    s = signing.LocalSigner(tmp_path)
    d, _, _ = _seal(tmp_path, s)
    assert evidence.SIGNATURE not in {f["path"] for f in evidence.list_files(d)}


# --------------------------------------------------------------- the verifier --
def _bundle(tmp_path: Path, signer: signing.LocalSigner | None) -> Path:
    d, _, _ = _seal(tmp_path, signer)
    return evidence.write_bundle(d, tmp_path / "evidence-c1.zip")


def _rezip(src: Path, dest: Path, edit) -> Path:  # noqa: ANN001
    files = {i.filename: zipfile.ZipFile(src).read(i.filename) for i in zipfile.ZipFile(src).infolist()}
    edit(files)
    with zipfile.ZipFile(dest, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return dest


def _key(s: signing.LocalSigner) -> list[bytes]:
    return [s.public_blob()]


TOOLS = ["cryptography", pytest.param("ssh-keygen", marks=needs_ssh)]


@pytest.mark.parametrize("tool", TOOLS)
def test_a_bundle_verifies_with_the_factorys_key_or_fingerprint(tmp_path, tool):
    s = signing.LocalSigner(tmp_path)
    z = _bundle(tmp_path, s)
    r = V.check(z, _key(s), tool=tool)
    assert r.code == V.OK, list(r.lines())
    assert V.check(z, fingerprints=[signing.fingerprint(s.public_blob())], tool=tool).code == V.OK
    # the folder itself verifies the same way
    assert V.check(tmp_path / "artifacts" / "c1", _key(s), tool=tool).code == V.OK


def test_without_a_trusted_key_the_signer_is_printed_never_trusted(tmp_path):
    s = signing.LocalSigner(tmp_path)
    r = V.check(_bundle(tmp_path, s))
    assert r.code == V.UNPINNED and r.signer == signing.fingerprint(s.public_blob())
    assert any("signer not checked" in w for ok, w in r.checks if not ok)


@pytest.mark.parametrize("tool", TOOLS)
def test_every_kind_of_tampering_fails(tmp_path, tool):
    s, intruder = signing.LocalSigner(tmp_path / "factory"), signing.LocalSigner(tmp_path / "intruder")
    z = _bundle(tmp_path, s)
    key = _key(s)

    def fails(edit, why: str) -> None:  # noqa: ANN001
        bad = _rezip(z, tmp_path / f"bad-{why}.zip", edit)
        r = V.check(bad, key, tool=tool)
        assert r.code == V.FAILED, (why, list(r.lines()))

    def a_file(f):  # noqa: ANN001, ANN202
        f["c1/events.jsonl"] = b'{"summary": "never happened"}\n'

    def rehashed(f):  # noqa: ANN001, ANN202 - the file, the manifest and SHA256SUMS all made to agree
        f["c1/events.jsonl"] = b"rewritten\n"
        m = json.loads(f["c1/manifest.json"])
        import hashlib

        for e in m["files"]:
            if e["path"] == "events.jsonl":
                e["sha256"], e["bytes"] = hashlib.sha256(b"rewritten\n").hexdigest(), 10
        f["c1/manifest.json"] = json.dumps(m).encode()
        f["c1/SHA256SUMS"] = (
            "".join(f"{e['sha256']}  {e['path']}\n" for e in m["files"]).encode()
            + f"{hashlib.sha256(f['c1/manifest.json']).hexdigest()}  manifest.json\n".encode()
        )

    def re_signed(f):  # noqa: ANN001, ANN202 - and signed again, by a key that isn't the factory's
        rehashed(f)
        f["c1/manifest.json.sig"] = intruder.sign(f["c1/manifest.json"]).encode()

    def unsigned(f):  # noqa: ANN001, ANN202
        del f["c1/manifest.json.sig"]

    def added(f):  # noqa: ANN001, ANN202
        f["c1/late.txt"] = b"written after the seal"

    def removed(f):  # noqa: ANN001, ANN202
        del f["c1/sessions/01-build.jsonl"]

    def escapes(f):  # noqa: ANN001, ANN202
        f["c1/../outside.txt"] = b"x"

    def two_roots(f):  # noqa: ANN001, ANN202
        f["c2/manifest.json"] = f["c1/manifest.json"]

    for edit in (a_file, rehashed, re_signed, unsigned, added, removed, escapes, two_roots):
        fails(edit, edit.__name__)


def test_a_bundle_from_before_signing_passes_only_when_asked(tmp_path):
    z = _bundle(tmp_path, None)
    assert V.check(z).code == V.FAILED
    assert V.check(z, allow_unsigned=True).code == V.OK


def test_the_command_line(tmp_path, capsys):
    s = signing.LocalSigner(tmp_path)
    z = _bundle(tmp_path, s)
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(signing.allowed_signers_line(s.public_blob()) + "\n")
    assert V.main([str(z), "--key", str(allowed), "--tool", "cryptography"]) == 0
    assert capsys.readouterr().out.strip().endswith("VERIFIED")
    assert V.main([str(z), "--key", signing.public_line(s.public_blob()), "--json", "--tool", "cryptography"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "VERIFIED"
    assert V.main([str(z), "--key", "not a key"]) == V.CANT_CHECK


def test_the_script_runs_with_python_alone(tmp_path):
    """No factory import, no package: the script is checked as a separate program (with -I)."""
    s = signing.LocalSigner(tmp_path)
    z = _bundle(tmp_path, s)
    res = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(SCRIPT), str(z), "--fingerprint", signing.fingerprint(s.public_blob())],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "agent_factory" not in SCRIPT.read_text().split('"""', 2)[2], "the verifier never imports the factory"


# ----------------------------------------------------------------- end to end --
async def test_a_delivered_changes_bundle_verifies_with_the_published_key(make_factory, tmp_path):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.get("/api/evidence/key", headers=MEMBER)).json()["fingerprint"] is None
        product = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()["product"]
        change_id = product["latest_change_id"]
        assert await wait_run(f, change_id) == ChangeStatus.awaiting_feedback
        key = (await c.get("/api/evidence/key", headers=MEMBER)).json()
        assert key["fingerprint"].startswith("SHA256:") and key["allowed_signers"].startswith(signing.IDENTITY)
        ev = (await c.get(f"/api/changes/{change_id}/evidence", headers=MEMBER)).json()
        assert ev["signed_by"] == key["fingerprint"] and ev["intact"]
        seal = evidence.latest_seal(f.store, change_id)
        assert seal and seal["signed_by"] == key["fingerprint"]

        r = await c.get(f"/api/changes/{change_id}/evidence/bundle", headers=MEMBER)
        bundle = tmp_path / "bundle.zip"
        bundle.write_bytes(r.content)
        got = V.check(bundle, V.keys_from_text(key["public_key"]), tool="cryptography")
        assert got.code == V.OK, list(got.lines())

        # tampered on the factory: the evidence page says the signature no longer holds
        folder = f.manager.ws.data_dir / "artifacts" / change_id
        (folder / evidence.SIGNATURE).write_text(signing.LocalSigner(tmp_path / "x").sign(b"other"))
        ev = (await c.get(f"/api/changes/{change_id}/evidence", headers=MEMBER)).json()
        assert not ev["intact"] and not ev["signature_ok"] and ev["signature_problem"]
        again = await c.get(f"/api/changes/{change_id}/evidence/bundle", headers=MEMBER)
        z = zipfile.ZipFile(io.BytesIO(again.content))
        assert f"{change_id}/manifest.json.sig" in z.namelist()
    await ctx.__aexit__(None, None, None)


def test_the_key_command_shows_and_rotates_only_when_confirmed(tmp_path, monkeypatch, capsys):
    from agent_factory import cli

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cli._evidence_key("show", False)
    shown = capsys.readouterr().out
    fp = signing.fingerprint(signing.LocalSigner(tmp_path).public_blob())
    assert f"fingerprint:     {fp}" in shown and "allowed_signers: agent-factory-evidence" in shown
    with pytest.raises(SystemExit):
        cli._evidence_key("rotate", False)
    assert signing.fingerprint(signing.LocalSigner(tmp_path).public_blob()) == fp, "nothing rotated unconfirmed"
    cli._evidence_key("rotate", True)
    out = json.loads(capsys.readouterr().out)
    assert out["retired"] == fp and out["fingerprint"] != fp
