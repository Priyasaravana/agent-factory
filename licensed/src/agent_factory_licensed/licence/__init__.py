# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""The offline licence key (ADR-0051). Checked here, never in core.

A key is one line: `AFL1.<payload>.<signature>`, both base64url without padding. The payload is JSON:

    {"v": 1, "id": "…", "licensee": "Acme Ltd", "features": ["sso", "siem"],
     "installations": 1, "issued": "2026-12-01", "expires": "2027-12-01"}

The signature is ed25519 over the bytes `AFL1.<payload>`, by one of the issuer keys trusted in
`issuers.txt` beside this file. No network: regulated and air-gapped buyers can't call an entitlement
service. Only the Licensor holds an issuer's private key (made by `new-issuer`, kept outside the repo).

The check never accepts a key it can't read in full: a bad prefix, a wrong signature, an issuer not in
the list, a missing field or a date it can't parse all refuse. After `expires` there are `GRACE_DAYS`
in which the key still works and says so, then it is refused. Refused means the licensed features don't
load; core (local sign-in included) keeps working.

This module uses only the standard library and `cryptography`, so the engine and the auth service can
both check a key without importing each other.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "AFL1"
GRACE_DAYS = 14
WARN_DAYS = 30
ENV_KEY = "AGENT_FACTORY_LICENCE"
ENV_FILE = "AGENT_FACTORY_LICENCE_FILE"
DEFAULT_FILE = "/run/secrets/agent_factory_licence"  # S-06: a mounted file, never an env value by default
ISSUERS_FILE = Path(__file__).with_name("issuers.txt")
FEATURES = ("sso", "siem", "kms", "compliance")
_KEY_TYPE = "ssh-ed25519"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------ issuers --
def _ssh_blob(raw: bytes) -> bytes:
    t = _KEY_TYPE.encode()
    return len(t).to_bytes(4, "big") + t + len(raw).to_bytes(4, "big") + raw


def public_line(raw: bytes, comment: str = "") -> str:
    return f"{_KEY_TYPE} {base64.b64encode(_ssh_blob(raw)).decode()} {comment}".strip()


def parse_issuer_line(line: str) -> bytes:
    """`ssh-ed25519 AAAA… comment` to the raw 32-byte key; raises ValueError on anything else."""
    parts = line.split()
    if len(parts) < 2 or parts[0] != _KEY_TYPE:
        raise ValueError("not an ssh-ed25519 public key")
    blob = base64.b64decode(parts[1], validate=True)
    expect = _ssh_blob(blob[-32:])
    if len(blob) != len(expect) or blob != expect:
        raise ValueError("not an ed25519 key")
    return blob[-32:]


def trusted_issuers(path: Path = ISSUERS_FILE) -> list[bytes]:
    """The issuer keys this build trusts. Comments and blank lines ignored; a line that isn't a key fails
    loudly (a typo must not silently trust nothing, or something else)."""
    if not path.is_file():
        return []
    keys = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            keys.append(parse_issuer_line(line))
        except ValueError as exc:
            raise ValueError(f"{path.name} line {n}: {exc}") from exc
    return keys


# -------------------------------------------------------------------- check --
@dataclass
class Licence:
    """The outcome of checking a key. `valid` only when the whole key held and it is within its term."""

    valid: bool
    problem: str = ""
    id: str = ""
    licensee: str = ""
    features: list[str] = field(default_factory=list)
    installations: int = 0
    issued: str = ""
    expires: str = ""
    in_grace: bool = False
    expires_soon: bool = False

    def grants(self, feature: str) -> bool:
        return self.valid and feature in self.features

    def summary(self) -> dict[str, object]:
        """What the factory may show: who holds it, what it grants, when it ends. Never key material."""
        out: dict[str, object] = {"valid": self.valid}
        if self.problem:
            out["problem"] = self.problem
        if self.licensee:
            out |= {
                "id": self.id,
                "licensee": self.licensee,
                "features": self.features,
                "installations": self.installations,
                "issued": self.issued,
                "expires": self.expires,
                "in_grace": self.in_grace,
                "expires_soon": self.expires_soon,
            }
        return out


def check(key: str | None, issuers: list[bytes], today: date | None = None) -> Licence:
    """Pure: does `key` hold under one of `issuers` on `today`?"""
    today = today or datetime.now(UTC).date()
    if not key or not key.strip():
        return Licence(False, "no licence key")
    if not issuers:
        return Licence(False, "this build trusts no licence issuer (issuers.txt is empty)")
    parts = key.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        return Licence(False, f"not a licence key (expected {PREFIX}.<payload>.<signature>)")
    try:
        payload, sig = _unb64(parts[1]), _unb64(parts[2])
    except ValueError:
        return Licence(False, "the key is not valid base64url")
    signed = f"{PREFIX}.{parts[1]}".encode()
    for raw in issuers:
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, signed)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        return Licence(False, "the key was not issued by a trusted issuer, or it was changed")
    try:
        doc = json.loads(payload)
        if doc.get("v") != 1:
            return Licence(False, f"unsupported licence version {doc.get('v')!r}")
        lic = Licence(
            valid=False,
            id=str(doc["id"]),
            licensee=str(doc["licensee"]),
            features=sorted({str(f) for f in doc["features"]}),
            installations=int(doc.get("installations", 1)),
            issued=str(doc["issued"]),
            expires=str(doc["expires"]),
        )
        expires = date.fromisoformat(lic.expires)
        date.fromisoformat(lic.issued)
    except (ValueError, KeyError, TypeError) as exc:
        return Licence(False, f"the key's contents can't be read: {exc}")
    if not lic.licensee or not lic.features:
        lic.problem = "the key names no licensee or no features"
        return lic
    if today > expires + timedelta(days=GRACE_DAYS):
        lic.problem = f"expired on {lic.expires} (and its {GRACE_DAYS} days' grace)"
        return lic
    lic.valid = True
    lic.in_grace = today > expires
    lic.expires_soon = not lic.in_grace and today > expires - timedelta(days=WARN_DAYS)
    if lic.in_grace:
        lic.problem = f"expired on {lic.expires}; works until {expires + timedelta(days=GRACE_DAYS)}, then refused"
    return lic


def read_key(env: dict[str, str] | None = None) -> str | None:
    """The key from `AGENT_FACTORY_LICENCE`, else the file `AGENT_FACTORY_LICENCE_FILE` names (default a
    mounted secret). None when neither is there."""
    env = dict(os.environ) if env is None else env
    if env.get(ENV_KEY, "").strip():
        return env[ENV_KEY].strip()
    path = Path(env.get(ENV_FILE) or DEFAULT_FILE)
    try:
        return path.read_text().strip() or None
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return None


def current(issuers: list[bytes] | None = None, env: dict[str, str] | None = None) -> Licence:
    try:
        issuers = trusted_issuers() if issuers is None else issuers
    except ValueError as exc:
        return Licence(False, str(exc))
    return check(read_key(env), issuers)


# ------------------------------------------------------- issuing (Licensor) --
def issue(
    private: Ed25519PrivateKey,
    licensee: str,
    features: list[str],
    expires: date,
    issued: date | None = None,
    installations: int = 1,
    licence_id: str | None = None,
) -> str:
    unknown = sorted(set(features) - set(FEATURES))
    if unknown:
        raise ValueError(f"unknown feature(s): {', '.join(unknown)} (known: {', '.join(FEATURES)})")
    doc = {
        "v": 1,
        "id": licence_id or str(uuid.uuid4()),
        "licensee": licensee,
        "features": sorted(set(features)),
        "installations": installations,
        "issued": (issued or datetime.now(UTC).date()).isoformat(),
        "expires": expires.isoformat(),
    }
    body = _b64(json.dumps(doc, separators=(",", ":"), sort_keys=True).encode())
    sig = private.sign(f"{PREFIX}.{body}".encode())
    return f"{PREFIX}.{body}.{_b64(sig)}"


def _raw_public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def new_issuer(out_dir: Path) -> str:
    """Make an issuer key pair in `out_dir` (keep it outside the repo and back it up); returns the public
    line to add to issuers.txt."""
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = out_dir / "issuer_key"
    if key_path.exists():
        raise FileExistsError(f"{key_path} exists; an issuer key is never overwritten")
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
    )
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    line = public_line(_raw_public(private), f"agent-factory-licence-issuer-{datetime.now(UTC):%Y%m%d}")
    (out_dir / "issuer_key.pub").write_text(line + "\n")
    return line


def load_issuer(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_ssh_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an ed25519 key")
    return key


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent-factory-licence", description=__doc__.split("\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ni = sub.add_parser("new-issuer", help="Licensor: make an issuer key pair (once; outside the repo)")
    ni.add_argument("--out", type=Path, required=True)
    iss = sub.add_parser("issue", help="Licensor: issue a key")
    iss.add_argument("--issuer-key", type=Path, required=True)
    iss.add_argument("--licensee", required=True)
    iss.add_argument("--features", required=True, help=f"comma-separated: {','.join(FEATURES)}")
    iss.add_argument("--expires", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    iss.add_argument("--installations", type=int, default=1)
    sub.add_parser("show", help="check this install's key and print what it grants (no key material)")
    args = p.parse_args(argv)
    if args.cmd == "new-issuer":
        print(new_issuer(args.out))
        print("Add that line to licensed/src/agent_factory_licensed/licence/issuers.txt", file=sys.stderr)
        return 0
    if args.cmd == "issue":
        key = issue(
            load_issuer(args.issuer_key),
            args.licensee,
            [f.strip() for f in args.features.split(",") if f.strip()],
            args.expires,
            installations=args.installations,
        )
        print(key)
        return 0
    lic = current()
    print(json.dumps(lic.summary(), indent=2))
    return 0 if lic.valid else 1


if __name__ == "__main__":
    sys.exit(main())
