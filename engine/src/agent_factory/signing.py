"""Signed evidence (ADR-0050, phase B): the factory signs every sealed manifest.

A seal (ADR-0023) hashes every evidence file into `manifest.json`; this module signs that manifest,
so a bundle downloaded and passed on can be checked by someone who trusts only the factory's
public key, not its database.

The format is OpenSSH's SSHSIG (`PROTOCOL.sshsig`), with an ed25519 key and namespace
`agent-factory-evidence`, written next to the manifest as `manifest.json.sig`. Anyone can check it
with the stock tool, without ours:

    echo "agent-factory-evidence namespaces=\"agent-factory-evidence\" <public key>" > allowed_signers
    ssh-keygen -Y verify -f allowed_signers -I agent-factory-evidence -n agent-factory-evidence \
        -s manifest.json.sig < manifest.json

`scripts/verify-evidence` does that and the file hashes in one step.

Keys (`<data>/keys/evidence/`): `signing_key` (OpenSSH private key, 0600, made on first use by the
engine's own user) and `signing_key.pub`. Rotating keeps every earlier public key under `retired/`,
so evidence sealed before still verifies on the factory. The private key lives in the factory's data
folder, so it is in the (encrypted) backups; someone with write access to that folder could sign. What
signing adds is that a copy outside the factory can be checked against a fingerprint published
elsewhere; a customer KMS signer (phase D) moves the key off the host. `Signer` is the seam for it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

NAMESPACE = "agent-factory-evidence"
IDENTITY = "agent-factory-evidence"  # the principal in an allowed_signers line
SIG_SUFFIX = ".sig"
HASH_ALG = "sha512"
_MAGIC = b"SSHSIG"
_KEY_TYPE = "ssh-ed25519"
_ARMOR_BEGIN = "-----BEGIN SSH SIGNATURE-----"
_ARMOR_END = "-----END SSH SIGNATURE-----"


# --------------------------------------------------------------- wire format --
def _string(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def _read_string(buf: bytes, at: int) -> tuple[bytes, int]:
    if at + 4 > len(buf):
        raise ValueError("truncated signature")
    (n,) = struct.unpack(">I", buf[at : at + 4])
    end = at + 4 + n
    if end > len(buf):
        raise ValueError("truncated signature")
    return buf[at + 4 : end], end


def key_blob(raw_public: bytes) -> bytes:
    """An ed25519 public key in SSH wire format."""
    return _string(_KEY_TYPE.encode()) + _string(raw_public)


def fingerprint(blob: bytes) -> str:
    """`SHA256:…` as `ssh-keygen -l` prints it."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def public_line(blob: bytes) -> str:
    return f"{_KEY_TYPE} {base64.b64encode(blob).decode()}"


def allowed_signers_line(blob: bytes) -> str:
    return f'{IDENTITY} namespaces="{NAMESPACE}" {public_line(blob)}'


def signed_data(message: bytes, namespace: str = NAMESPACE) -> bytes:
    """What SSHSIG actually signs: the preamble, namespace and the message's SHA-512."""
    return (
        _MAGIC
        + _string(namespace.encode())
        + _string(b"")
        + _string(HASH_ALG.encode())
        + _string(hashlib.sha512(message).digest())
    )


def armor(pub_blob: bytes, raw_signature: bytes, namespace: str = NAMESPACE) -> str:
    blob = (
        _MAGIC
        + struct.pack(">I", 1)
        + _string(pub_blob)
        + _string(namespace.encode())
        + _string(b"")
        + _string(HASH_ALG.encode())
        + _string(_string(_KEY_TYPE.encode()) + _string(raw_signature))
    )
    b64 = base64.b64encode(blob).decode()
    lines = [b64[i : i + 70] for i in range(0, len(b64), 70)]
    return "\n".join([_ARMOR_BEGIN, *lines, _ARMOR_END]) + "\n"


@dataclass(frozen=True)
class Parsed:
    pub_blob: bytes
    namespace: str
    hash_alg: str
    key_type: str
    raw_signature: bytes

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.pub_blob)


def parse(text: str) -> Parsed:
    """An armored SSHSIG signature; raises ValueError on anything else."""
    body = text.strip()
    if not (body.startswith(_ARMOR_BEGIN) and body.endswith(_ARMOR_END)):
        raise ValueError("not an SSH signature")
    b64 = "".join(body[len(_ARMOR_BEGIN) : -len(_ARMOR_END)].split())
    try:
        buf = base64.b64decode(b64, validate=True)
    except ValueError as exc:
        raise ValueError("not an SSH signature") from exc
    if buf[:6] != _MAGIC or len(buf) < 10 or struct.unpack(">I", buf[6:10])[0] != 1:
        raise ValueError("unsupported SSH signature version")
    at = 10
    pub, at = _read_string(buf, at)
    ns, at = _read_string(buf, at)
    _reserved, at = _read_string(buf, at)
    alg, at = _read_string(buf, at)
    sig, at = _read_string(buf, at)
    if at != len(buf):
        raise ValueError("trailing data in signature")
    kt, s_at = _read_string(sig, 0)
    raw, s_end = _read_string(sig, s_at)
    if s_end != len(sig):
        raise ValueError("trailing data in signature")
    return Parsed(pub, ns.decode(), alg.decode(), kt.decode(), raw)


def raw_public(pub_blob: bytes) -> bytes:
    kt, at = _read_string(pub_blob, 0)
    raw, end = _read_string(pub_blob, at)
    if kt.decode() != _KEY_TYPE or len(raw) != 32 or end != len(pub_blob):
        raise ValueError("not an ed25519 key")
    return raw


def blob_from_public_line(line: str) -> bytes:
    """`ssh-ed25519 AAAA… [comment]` (or an allowed_signers line) to the key's wire blob."""
    parts = line.split()
    for i, p in enumerate(parts[:-1]):
        if p == _KEY_TYPE:
            blob = base64.b64decode(parts[i + 1], validate=True)
            raw_public(blob)  # refuses anything but an ed25519 key
            return blob
    raise ValueError("no ssh-ed25519 public key in the line")


# ------------------------------------------------------------------ verifying --
@dataclass
class Check:
    """A signature checked against a set of trusted keys. `ok` only when all of it holds."""

    present: bool
    valid: bool = False  # the signature matches the message under the key it names
    trusted: bool = False  # that key is one of the trusted keys
    signer: str | None = None  # the key's fingerprint
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.present and self.valid and self.trusted


def check(message: bytes, sig_text: str | None, trusted: set[bytes]) -> Check:
    """Pure: does `sig_text` sign `message` with one of the `trusted` key blobs?"""
    if sig_text is None:
        return Check(present=False, reason="no signature")
    try:
        p = parse(sig_text)
        raw = raw_public(p.pub_blob)
    except ValueError as exc:
        return Check(present=True, reason=str(exc))
    c = Check(present=True, signer=p.fingerprint, trusted=p.pub_blob in trusted)
    if p.namespace != NAMESPACE:
        c.reason = f"signed for namespace {p.namespace!r}, not {NAMESPACE!r}"
        return c
    if p.key_type != _KEY_TYPE or p.hash_alg not in ("sha512", "sha256"):
        c.reason = "unsupported signature algorithm"
        return c
    data = (
        signed_data(message)
        if p.hash_alg == "sha512"
        else (
            _MAGIC
            + _string(p.namespace.encode())
            + _string(b"")
            + _string(b"sha256")
            + _string(hashlib.sha256(message).digest())
        )
    )
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(p.raw_signature, data)
    except InvalidSignature:
        c.reason = "the signature does not match"
        return c
    c.valid = True
    if not c.trusted:
        c.reason = f"signed by {p.fingerprint}, which is not a trusted key"
    return c


# --------------------------------------------------------------------- signing --
class Signer(Protocol):
    """Signs evidence. Local key now; a customer KMS later (phase D) implements the same."""

    def public_blob(self) -> bytes: ...

    def sign(self, message: bytes) -> str:
        """The armored SSHSIG signature of `message`."""
        ...

    def trusted(self) -> set[bytes]:
        """Every key this factory has signed with: the current one and those it rotated out."""
        ...


class LocalSigner:
    """An ed25519 key in the factory's data folder (`<data>/keys/evidence/`)."""

    def __init__(self, data_dir: Path | str) -> None:
        self.dir = Path(data_dir) / "keys" / "evidence"
        self.key_path = self.dir / "signing_key"
        self.pub_path = self.dir / "signing_key.pub"
        self.retired_dir = self.dir / "retired"
        self._private: Ed25519PrivateKey | None = None

    # -- key on disk
    def _load_or_create(self) -> Ed25519PrivateKey:
        if self._private is not None:
            return self._private
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self._private = self._load()
        except FileNotFoundError:
            self._private = self._create()
        return self._private

    def _load(self) -> Ed25519PrivateKey:
        key = serialization.load_ssh_private_key(self.key_path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{self.key_path} is not an ed25519 key")
        return key

    def _create(self) -> Ed25519PrivateKey:
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        )
        try:  # O_EXCL: two seals at once never make two keys; the loser reads the winner's
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self._load()
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        blob = key_blob(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.pub_path.write_text(public_line(blob) + f" {IDENTITY}\n")
        return key

    # -- Signer
    def public_blob(self) -> bytes:
        k = self._load_or_create()
        return key_blob(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    def sign(self, message: bytes) -> str:
        k = self._load_or_create()
        return armor(self.public_blob(), k.sign(signed_data(message)))

    def retired(self) -> list[bytes]:
        if not self.retired_dir.is_dir():
            return []
        out = []
        for p in sorted(self.retired_dir.glob("*.pub")):
            try:
                out.append(blob_from_public_line(p.read_text()))
            except ValueError:
                continue
        return out

    def trusted(self) -> set[bytes]:
        keys = set(self.retired())
        if self.key_path.is_file():
            keys.add(self.public_blob())
        return keys

    def rotate(self) -> tuple[str | None, str]:
        """Retire the current key (its public half is kept, so earlier evidence still verifies) and
        make a new one. Returns (old fingerprint or None, new fingerprint)."""
        old = None
        if self.key_path.is_file():
            blob = self.public_blob()
            old = fingerprint(blob)
            self.retired_dir.mkdir(parents=True, exist_ok=True)
            safe = old.removeprefix("SHA256:").replace("/", "_").replace("+", "-")
            (self.retired_dir / f"{safe}.pub").write_text(public_line(blob) + f" {IDENTITY}\n")
            self.key_path.unlink()
            self.pub_path.unlink(missing_ok=True)
            self._private = None
        return old, fingerprint(self.public_blob())


def signer_for(data_dir: Path | str) -> Signer:
    """The local key, unless an installed extension provides the evidence signer (ADR-0051: a customer
    KMS in the licensed edition). Verifying never changes: `check` against the signer's trusted keys."""
    from agent_factory import extensions  # late: extensions imports Signer from here

    return extensions.active().signer(data_dir) or LocalSigner(data_dir)
