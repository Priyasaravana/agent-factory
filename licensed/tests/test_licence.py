# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""The offline licence key (ADR-0051): what it grants, and every way it is refused."""

from __future__ import annotations

import base64
import json
from datetime import date

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_factory_licensed import licence

TODAY = date(2026, 12, 1)


@pytest.fixture
def issuer():
    return Ed25519PrivateKey.generate()


def _raw(key: Ed25519PrivateKey) -> bytes:
    return licence._raw_public(key)


def _key(issuer, **kw) -> str:
    args = {"licensee": "Acme Ltd", "features": ["sso", "siem"], "expires": date(2027, 12, 1), "issued": TODAY}
    return licence.issue(issuer, **(args | kw))


def test_a_key_from_a_trusted_issuer_grants_its_features(issuer):
    lic = licence.check(_key(issuer), [_raw(issuer)], TODAY)
    assert lic.valid and lic.problem == ""
    assert lic.grants("sso") and lic.grants("siem") and not lic.grants("kms")
    assert lic.licensee == "Acme Ltd" and lic.expires == "2027-12-01"


def test_the_summary_never_holds_key_material(issuer):
    key = _key(issuer)
    summary = json.dumps(licence.check(key, [_raw(issuer)], TODAY).summary())
    for part in key.split(".")[1:]:
        assert part not in summary


@pytest.mark.parametrize(
    "mangle, why",
    [
        (lambda k: "", "no licence key"),
        (lambda k: "AFL2." + k.split(".", 1)[1], "not a licence key"),
        (lambda k: k.rsplit(".", 1)[0], "not a licence key"),
        (lambda k: k[:-4] + ("AAAA" if not k.endswith("AAAA") else "BBBB"), "not issued by a trusted issuer"),
    ],
)
def test_a_missing_malformed_or_tampered_key_is_refused(issuer, mangle, why):
    lic = licence.check(mangle(_key(issuer)), [_raw(issuer)], TODAY)
    assert not lic.valid and why in lic.problem


def test_a_payload_edited_to_add_a_feature_is_refused(issuer):
    head, body, sig = _key(issuer).split(".")
    doc = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    doc["features"].append("kms")
    forged = base64.urlsafe_b64encode(json.dumps(doc).encode()).decode().rstrip("=")
    lic = licence.check(f"{head}.{forged}.{sig}", [_raw(issuer)], TODAY)
    assert not lic.valid and "not issued by a trusted issuer" in lic.problem


def test_a_key_from_another_issuer_is_refused(issuer):
    other = Ed25519PrivateKey.generate()
    assert not licence.check(_key(other), [_raw(issuer)], TODAY).valid


def test_a_build_that_trusts_no_issuer_refuses_every_key(issuer):
    lic = licence.check(_key(issuer), [], TODAY)
    assert not lic.valid and "trusts no licence issuer" in lic.problem


def test_expiry_warns_then_grace_then_refused(issuer):
    key = _key(issuer, expires=date(2026, 12, 20))
    soon = licence.check(key, [_raw(issuer)], TODAY)
    assert soon.valid and soon.expires_soon and not soon.in_grace
    grace = licence.check(key, [_raw(issuer)], date(2026, 12, 30))
    assert grace.valid and grace.in_grace and "works until 2027-01-03" in grace.problem
    gone = licence.check(key, [_raw(issuer)], date(2027, 1, 4))
    assert not gone.valid and "expired on 2026-12-20" in gone.problem and not gone.grants("sso")


def test_only_known_features_can_be_issued(issuer):
    with pytest.raises(ValueError, match="unknown feature"):
        _key(issuer, features=["sso", "everything"])


def test_the_key_is_read_from_the_environment_or_a_mounted_file(tmp_path):
    f = tmp_path / "licence"
    f.write_text("AFL1.x.y\n")
    assert licence.read_key({"AGENT_FACTORY_LICENCE": " AFL1.a.b "}) == "AFL1.a.b"
    assert licence.read_key({"AGENT_FACTORY_LICENCE_FILE": str(f)}) == "AFL1.x.y"
    assert licence.read_key({"AGENT_FACTORY_LICENCE_FILE": str(tmp_path / "none")}) is None


def test_issuers_file_lists_keys_and_fails_loudly_on_a_bad_line(tmp_path, issuer):
    good = tmp_path / "issuers.txt"
    good.write_text("# comment\n\n" + licence.public_line(_raw(issuer), "test") + "\n")
    assert licence.trusted_issuers(good) == [_raw(issuer)]
    bad = tmp_path / "bad.txt"
    bad.write_text("ssh-rsa AAAAB3Nza not-ed25519\n")
    with pytest.raises(ValueError, match="line 1"):
        licence.trusted_issuers(bad)
    assert not licence.current(env={"AGENT_FACTORY_LICENCE": _key(issuer)}, issuers=[]).valid


def test_the_shipped_issuers_file_parses():
    licence.trusted_issuers()  # empty until the Licensor adds a key; never malformed


def test_new_issuer_writes_a_private_key_once_and_its_keys_check(tmp_path):
    line = licence.new_issuer(tmp_path / "iss")
    key_path = tmp_path / "iss" / "issuer_key"
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(FileExistsError):
        licence.new_issuer(tmp_path / "iss")
    priv = licence.load_issuer(key_path)
    key = licence.issue(priv, "Acme", ["siem"], date(2027, 1, 1), issued=TODAY)
    assert licence.check(key, [licence.parse_issuer_line(line)], TODAY).valid
