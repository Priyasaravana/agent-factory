#!/usr/bin/env python3
"""Check an Agent Software Factory evidence bundle, away from the factory (ADR-0050).

Needs only Python 3.10+ and, for the signature, either OpenSSH's `ssh-keygen` or the
`cryptography` package. It does not install or import the factory.

    scripts/verify-evidence.py evidence-<change>.zip --key factory-evidence.pub
    scripts/verify-evidence.py evidence-<change>.zip --fingerprint SHA256:…
    scripts/verify-evidence.py artifacts/<change>/ --key allowed_signers

What it checks:
  1. the bundle is one folder, with no path that leaves it and no links;
  2. every file the manifest lists is there, with its size and SHA-256, and nothing else is;
  3. SHA256SUMS says the same as the manifest (so `sha256sum -c` agrees);
  4. manifest.json.sig is an SSH signature (namespace agent-factory-evidence) over manifest.json;
  5. the key that signed it is the one you trust: --key (a public key file, an allowed_signers
     file or a `ssh-ed25519 AAAA…` line; several keys allowed) or --fingerprint. Get it from the
     factory's Settings or `GET /api/evidence/key`, ideally published somewhere the factory can't
     change (a README, a repo). A key found inside the bundle is never trusted on its own.

Exit codes: 0 verified; 1 failed (tampered, missing, unsigned, signed by another key);
2 can't check (bad arguments, no tool to check the signature); 3 intact and validly signed,
but you didn't say which key to trust (the signer's fingerprint is printed: compare it).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO

NAMESPACE = "agent-factory-evidence"
MANIFEST, SUMS = "manifest.json", "SHA256SUMS"
SIGNATURE = MANIFEST + ".sig"
SCHEMA = "agent-factory/evidence-manifest/v1"
_MAGIC = b"SSHSIG"
_KEY_TYPE = "ssh-ed25519"

OK, FAILED, CANT_CHECK, UNPINNED = 0, 1, 2, 3


# ------------------------------------------------------------------ the bundle --
class Bundle:
    """The files of a zip (read in place, never extracted) or a folder, by POSIX path under its root."""

    def __init__(
        self, names: dict[str, int], opener: Callable[[str], IO[bytes]], root: str
    ) -> None:
        self.sizes = names
        self._open = opener
        self.root = root

    def read(self, name: str) -> bytes:
        with self._open(name) as fh:
            return fh.read()

    def sha256(self, name: str) -> str:
        h = hashlib.sha256()
        with self._open(name) as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()


def _safe(name: str) -> bool:
    p = PurePosixPath(name)
    return (
        not p.is_absolute()
        and ".." not in p.parts
        and "\\" not in name
        and name.strip() == name
    )


def open_bundle(path: Path) -> Bundle:
    if path.is_dir():
        names: dict[str, int] = {}
        for p in sorted(path.rglob("*")):
            rel = p.relative_to(path).as_posix()
            if p.is_symlink():
                raise ValueError(f"{rel} is a link: a bundle holds files only")
            if p.is_file():
                names[rel] = p.stat().st_size
        return Bundle(names, lambda n: (path / n).open("rb"), path.name)
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is neither a folder nor a zip")
    z = zipfile.ZipFile(path)
    roots, names = set(), {}
    for info in z.infolist():
        if info.is_dir():
            continue
        if not _safe(info.filename):
            raise ValueError(f"unsafe path in the zip: {info.filename!r}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"{info.filename} is a link: a bundle holds files only")
        root, _, rest = info.filename.partition("/")
        if not rest:
            raise ValueError(f"{info.filename} is outside the bundle's folder")
        roots.add(root)
        if rest in names:
            raise ValueError(f"{rest} is in the zip twice")
        names[rest] = info.file_size
    if len(roots) != 1:
        raise ValueError(
            f"a bundle is one folder; this zip has {len(roots) or 'no'} top-level folders"
        )
    (root,) = roots
    return Bundle(names, lambda n: z.open(f"{root}/{n}"), root)


# ------------------------------------------------------------- SSH signatures --
def _read_string(buf: bytes, at: int) -> tuple[bytes, int]:
    if at + 4 > len(buf):
        raise ValueError("truncated signature")
    (n,) = struct.unpack(">I", buf[at : at + 4])
    if at + 4 + n > len(buf):
        raise ValueError("truncated signature")
    return buf[at + 4 : at + 4 + n], at + 4 + n


def _string(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def fingerprint(blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip(
        "="
    )


@dataclass
class Sig:
    pub_blob: bytes
    namespace: str
    hash_alg: str
    raw: bytes


def parse_signature(text: str) -> Sig:
    begin, end = "-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----"
    body = text.strip()
    if not (body.startswith(begin) and body.endswith(end)):
        raise ValueError("not an SSH signature")
    buf = base64.b64decode("".join(body[len(begin) : -len(end)].split()), validate=True)
    if buf[:6] != _MAGIC or len(buf) < 10 or struct.unpack(">I", buf[6:10])[0] != 1:
        raise ValueError("unsupported SSH signature version")
    pub, at = _read_string(buf, 10)
    ns, at = _read_string(buf, at)
    _, at = _read_string(buf, at)
    alg, at = _read_string(buf, at)
    sig, at = _read_string(buf, at)
    kt, s = _read_string(sig, 0)
    raw, s = _read_string(sig, s)
    pkt, p = _read_string(pub, 0)
    praw, p = _read_string(pub, p)
    if at != len(buf) or s != len(sig) or p != len(pub):
        raise ValueError("trailing data in signature")
    if kt.decode() != _KEY_TYPE or pkt.decode() != _KEY_TYPE or len(praw) != 32:
        raise ValueError("only ssh-ed25519 signatures are accepted")
    return Sig(pub, ns.decode(), alg.decode(), raw)


def keys_from_text(text: str) -> list[bytes]:
    """Every ssh-ed25519 key in a public key file, an allowed_signers file or one line."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        for i, p in enumerate(parts[:-1]):
            if p == _KEY_TYPE:
                try:
                    out.append(base64.b64decode(parts[i + 1], validate=True))
                except ValueError:
                    pass
                break
    return out


def _crypto_check(message: bytes, sig: Sig) -> bool | None:
    """True/False with the `cryptography` package; None when it isn't installed."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return None
    hfun = {"sha512": hashlib.sha512, "sha256": hashlib.sha256}.get(sig.hash_alg)
    if hfun is None:
        return False
    data = (
        _MAGIC
        + _string(sig.namespace.encode())
        + _string(b"")
        + _string(sig.hash_alg.encode())
    )
    data += _string(hfun(message).digest())
    _, at = _read_string(sig.pub_blob, 0)
    raw_pub, _ = _read_string(sig.pub_blob, at)
    try:
        Ed25519PublicKey.from_public_bytes(raw_pub).verify(sig.raw, data)
    except InvalidSignature:
        return False
    return True


def _ssh_keygen_check(message: bytes, sig_text: str, pub_blob: bytes) -> bool | None:
    """True/False with OpenSSH's ssh-keygen; None when it isn't installed."""
    tool = shutil.which("ssh-keygen")
    if tool is None:
        return None
    with tempfile.TemporaryDirectory() as d:
        allowed = Path(d) / "allowed_signers"
        allowed.write_text(
            f'{NAMESPACE} namespaces="{NAMESPACE}" {_KEY_TYPE} {base64.b64encode(pub_blob).decode()}\n'
        )
        sig = Path(d) / SIGNATURE
        sig.write_text(sig_text)
        cmd = [
            tool,
            "-Y",
            "verify",
            "-f",
            str(allowed),
            "-I",
            NAMESPACE,
            "-n",
            NAMESPACE,
            "-s",
            str(sig),
        ]
        try:
            res = subprocess.run(cmd, check=False, input=message, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            return None
        return res.returncode == 0


def signature_valid(
    message: bytes, sig_text: str, sig: Sig, prefer: str = "auto"
) -> tuple[bool | None, str]:
    """(valid, by which tool). valid is None when neither tool is available."""
    order = {
        "auto": ("ssh-keygen", "cryptography"),
        "ssh-keygen": ("ssh-keygen",),
        "cryptography": ("cryptography",),
    }
    for name in order[prefer]:
        got = (
            _ssh_keygen_check(message, sig_text, sig.pub_blob)
            if name == "ssh-keygen"
            else _crypto_check(message, sig)
        )
        if got is not None:
            return got, name
    return None, ""


# ------------------------------------------------------------------- checking --
@dataclass
class Report:
    checks: list[tuple[bool, str]] = field(default_factory=list)
    signer: str | None = None
    code: int = OK

    def ok(self, what: str) -> None:
        self.checks.append((True, what))

    def fail(self, what: str, code: int = FAILED) -> None:
        self.checks.append((False, what))
        rank = {OK: 0, UNPINNED: 1, CANT_CHECK: 2, FAILED: 3}
        if rank[code] > rank[self.code]:
            self.code = code

    def lines(self) -> Iterator[str]:
        for good, what in self.checks:
            yield f"{'ok  ' if good else 'FAIL'}  {what}"


def check(
    path: Path,
    trusted: list[bytes] | None = None,
    fingerprints: list[str] | None = None,
    allow_unsigned: bool = False,
    tool: str = "auto",
) -> Report:
    r = Report()
    try:
        b = open_bundle(path)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        r.fail(str(exc))
        return r
    r.ok(f"one folder, {b.root}/, files only")
    if MANIFEST not in b.sizes:
        r.fail("no manifest.json: not an evidence bundle")
        return r
    manifest_bytes = b.read(MANIFEST)
    try:
        m = json.loads(manifest_bytes)
        listed = {f["path"]: (f["sha256"], int(f["bytes"])) for f in m["files"]}
    except (ValueError, KeyError, TypeError):
        r.fail("manifest.json is not a valid evidence manifest")
        return r
    if m.get("_type") != SCHEMA:
        r.fail(f"manifest type {m.get('_type')!r}, expected {SCHEMA!r}")
        return r

    # 2. files against the manifest
    bad = []
    for name, (sha, size) in sorted(listed.items()):
        if name not in b.sizes:
            bad.append(f"missing: {name}")
        elif b.sizes[name] != size or b.sha256(name) != sha:
            bad.append(f"changed: {name}")
    extra = sorted(
        n for n in b.sizes if n not in listed and n not in (MANIFEST, SUMS, SIGNATURE)
    )
    bad += [f"not in the manifest: {n}" for n in extra]
    if bad:
        for x in bad:
            r.fail(x)
    else:
        r.ok(
            f"{len(listed)} files match the manifest's SHA-256 and sizes, nothing added"
        )

    # 3. SHA256SUMS agrees with the manifest
    if SUMS in b.sizes:
        want = {n: s for n, (s, _) in listed.items()} | {
            MANIFEST: hashlib.sha256(manifest_bytes).hexdigest()
        }
        got = {}
        for line in b.read(SUMS).decode(errors="replace").splitlines():
            if line.strip():
                sha, _, name = line.partition("  ")
                got[name] = sha
        if got == want:
            r.ok("SHA256SUMS matches the manifest")
        else:
            r.fail("SHA256SUMS does not match the manifest")
    else:
        r.fail("no SHA256SUMS")

    # 4. the signature
    if SIGNATURE not in b.sizes:
        if allow_unsigned:
            r.ok(
                "not signed (sealed before signing): checked by its hashes only, as you asked"
            )
        else:
            r.fail(
                "not signed: a sealed bundle from a factory with ADR-0050 carries manifest.json.sig"
            )
        return r
    sig_text = b.read(SIGNATURE).decode(errors="replace")
    try:
        sig = parse_signature(sig_text)
    except ValueError as exc:
        r.fail(f"manifest.json.sig: {exc}")
        return r
    r.signer = fingerprint(sig.pub_blob)
    if sig.namespace != NAMESPACE:
        r.fail(f"signed for {sig.namespace!r}, not {NAMESPACE!r}")
        return r
    valid, by = signature_valid(manifest_bytes, sig_text, sig, tool)
    if valid is None:
        r.fail(
            "can't check the signature: install OpenSSH (ssh-keygen) or `pip install cryptography`",
            CANT_CHECK,
        )
        return r
    if not valid:
        r.fail(f"the signature does not match manifest.json (checked with {by})")
        return r
    r.ok(f"manifest.json is signed by {r.signer} (checked with {by})")

    # 5. by the key you trust
    pinned = {fingerprint(k) for k in trusted or []} | set(fingerprints or [])
    if not pinned:
        r.fail(
            f"signer not checked: pass --key or --fingerprint; compare {r.signer} with the factory's",
            UNPINNED,
        )
    elif r.signer in pinned:
        r.ok("the signer is a key you trust")
    else:
        r.fail(f"signed by {r.signer}, which is not a key you trust")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify-evidence",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument(
        "bundle", type=Path, help="evidence-<change>.zip, or an evidence folder"
    )
    ap.add_argument(
        "--key",
        action="append",
        default=[],
        help="trusted public key: a file or a `ssh-ed25519 …` line",
    )
    ap.add_argument(
        "--fingerprint",
        action="append",
        default=[],
        help="trusted key fingerprint, SHA256:…",
    )
    ap.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="accept a bundle sealed before signing (hashes only)",
    )
    ap.add_argument(
        "--tool", choices=("auto", "ssh-keygen", "cryptography"), default="auto"
    )
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    keys: list[bytes] = []
    for k in a.key:
        text = Path(k).read_text() if os.path.isfile(k) else k
        found = keys_from_text(text)
        if not found:
            print(f"verify-evidence: no ssh-ed25519 key in {k!r}", file=sys.stderr)
            return CANT_CHECK
        keys += found
    r = check(a.bundle, keys, a.fingerprint, a.allow_unsigned, a.tool)
    verdict = {
        OK: "VERIFIED",
        FAILED: "FAILED",
        CANT_CHECK: "NOT CHECKED",
        UNPINNED: "SIGNER NOT CHECKED",
    }[r.code]
    if a.json:
        out = {
            "verdict": verdict,
            "signer": r.signer,
            "checks": [{"ok": g, "what": w} for g, w in r.checks],
        }
        print(json.dumps(out, indent=2))
    else:
        print("\n".join(r.lines()))
        print(verdict)
    return r.code


if __name__ == "__main__":
    sys.exit(main())
