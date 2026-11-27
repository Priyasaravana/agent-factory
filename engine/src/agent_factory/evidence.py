"""Evidence manifest (ADR-0023): one sealed, verifiable record of what a change produced.

Whenever a change stops (delivered, failed, held, waiting on a person, cancelled) the
engine **seals** its evidence folder `artifacts/<run>/`:

1. `events.jsonl`: the change's full event log, exactly as stored (already redacted).
2. `spec/`: the spec the change worked to (`docs/spec.md`, `docs/requirements.yaml`),
   so the bundle shows what was promised next to what was proven.
3. `pillars.json`: the evidence grouped by quality pillar (ADR-0024).
4. `manifest.json`: run, order, workflow version, commit, image, outcome, the artifact
   ledger's summary (`ledger.json`, ADR-0037), and every file with its SHA-256 and size.
5. `SHA256SUMS`: the same hashes in coreutils format, so anyone can check a
   downloaded bundle with `sha256sum -c SHA256SUMS`, without our tools.
6. `manifest.json.sig`: the manifest signed with the factory's evidence key (ADR-0050,
   `signing.py`, OpenSSH SSHSIG), so a copy can be checked by someone who trusts only the
   factory's public key: `scripts/verify-evidence`, or `ssh-keygen -Y verify`.

The manifest's own SHA-256 is recorded as a decision event in the database. On
every read the engine re-hashes the files and compares (`verify`, a pure function
of the folder): a changed, missing or added file is reported, never hidden.

Scope, stated honestly: the hashes detect evidence that changed after it was sealed
(by accident, a bug, or casual editing); the signature lets a downloaded copy be
checked away from the factory. The signing key lives in the data folder, so someone
with write access to it (and the database) could still re-seal and re-sign; a
customer KMS signer (phase D) and external anchoring close that (ADR-0050).

Never in a manifest or bundle: the hidden scenarios (they live outside the
artifacts folder) and secret values (every stored text goes through REDACTOR).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
SUMS = "SHA256SUMS"
PILLAR_INDEX = "pillars.json"  # evidence grouped by quality pillar (ADR-0024)
SCHEMA = "agent-factory/evidence-manifest/v1"
SIGNATURE = MANIFEST + ".sig"  # ADR-0050
_NOT_HASHED = {MANIFEST, SUMS, SIGNATURE}  # written last; they describe the others


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def list_files(folder: Path) -> list[dict[str, Any]]:
    """Every evidence file under the folder (sorted, POSIX paths), with hash and size."""
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_file() and not p.is_symlink() and rel not in _NOT_HASHED:
            out.append({"path": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size})
    return out


def build_manifest(folder: Path, facts: dict[str, Any], sealed_at: datetime) -> dict[str, Any]:
    """The manifest for the folder as it is now. `facts`: run, order, source, image, outcome."""
    files = list_files(folder)
    return {
        "_type": SCHEMA,
        "sealed_at": sealed_at.astimezone(UTC).isoformat(),
        **facts,
        "files": files,
        "totals": {"files": len(files), "bytes": sum(f["bytes"] for f in files)},
    }


def build_pillar_index(folder: Path) -> dict[str, Any]:
    """Evidence grouped by quality pillar (ADR-0024): each pillar's readiness signals
    (from readiness.json, when the change reached the Quality gate station) and its files."""
    from agent_factory.pillars import BY_ID, IDS, evidence_pillar
    from agent_factory.readiness import PILLAR_OF

    try:
        signals = json.loads((folder / "readiness.json").read_text()).get("signals", [])
    except (OSError, ValueError):
        signals = []
    index: dict[str, dict[str, Any]] = {p: {"id": p, "title": BY_ID[p].title, "signals": [], "files": []} for p in IDS}
    for sig in signals:
        p = sig.get("pillar") or PILLAR_OF.get(str(sig.get("id")))
        if p in index:
            index[p]["signals"].append({"id": sig.get("id"), "ok": bool(sig.get("ok"))})
    other = []
    for f in list_files(folder):
        if f["path"] == PILLAR_INDEX:
            continue
        p = evidence_pillar(f["path"])
        (index[p]["files"] if p in index else other).append(f["path"])
    for v in index.values():
        v["passed"] = sum(1 for x in v["signals"] if x["ok"])
        v["applicable"] = len(v["signals"])
    return {"_type": "agent-factory/pillar-index/v1", "pillars": list(index.values()), "other_files": other}


def write_manifest(folder: Path, manifest: dict[str, Any]) -> str:
    """Write manifest.json and SHA256SUMS; return the manifest's SHA-256."""
    body = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    (folder / MANIFEST).write_bytes(body)
    sums = [f"{f['sha256']}  {f['path']}" for f in manifest["files"]]
    sums.append(f"{hashlib.sha256(body).hexdigest()}  {MANIFEST}")
    (folder / SUMS).write_text("\n".join(sums) + "\n")
    return hashlib.sha256(body).hexdigest()


def sign_manifest(folder: Path, signer: Any) -> str:
    """Sign the manifest as written (ADR-0050); returns the signing key's fingerprint."""
    from agent_factory import signing

    (folder / SIGNATURE).write_text(signer.sign((folder / MANIFEST).read_bytes()))
    return signing.fingerprint(signer.public_blob())


@dataclass
class Verification:
    sealed: bool
    manifest_ok: bool = False  # manifest.json matches the digest recorded in the database
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)  # written after the seal
    # ADR-0050: sealed with a signature (recorded at the seal); a seal from before signing has none
    signed: bool = False
    signature_ok: bool = False  # the signature matches, by the key the seal recorded, which is trusted
    signer: str | None = None
    signature_problem: str = ""

    @property
    def intact(self) -> bool:
        return (
            self.sealed
            and self.manifest_ok
            and not (self.changed or self.missing or self.added)
            and (self.signature_ok or not self.signed)
        )


def verify(
    folder: Path,
    recorded_sha256: str | None,
    signed_by: str | None = None,
    trusted: set[bytes] | None = None,
) -> Verification:
    """Re-hash the folder against its manifest and the manifest against the recorded digest.
    `signed_by`: the key fingerprint the seal recorded (ADR-0050); the signature must then be there,
    match, be by that key and that key be one of `trusted` (the factory's keys). A seal that recorded
    no signature (sealed before signing) is checked by its hashes only, and says so (`signed` False)."""
    from agent_factory import signing

    mpath = folder / MANIFEST
    if not recorded_sha256 or not mpath.is_file():
        return Verification(sealed=False)
    v = Verification(sealed=True, manifest_ok=sha256_file(mpath) == recorded_sha256, signed=bool(signed_by))
    if signed_by:
        sig = folder / SIGNATURE
        c = signing.check(mpath.read_bytes(), sig.read_text() if sig.is_file() else None, trusted or set())
        v.signer = c.signer
        v.signature_ok = c.ok and c.signer == signed_by
        v.signature_problem = (
            c.reason if not c.ok else ("" if c.signer == signed_by else f"signed by {c.signer}, not {signed_by}")
        )
    try:
        listed = {f["path"]: f["sha256"] for f in json.loads(mpath.read_text())["files"]}
    except (ValueError, KeyError, TypeError):
        v.manifest_ok = False
        return v
    now = {f["path"]: f["sha256"] for f in list_files(folder)}
    v.changed = sorted(p for p in listed if p in now and now[p] != listed[p])
    v.missing = sorted(p for p in listed if p not in now)
    v.added = sorted(p for p in now if p not in listed)
    return v


def _files_inside(folder: Path) -> set[str]:
    """POSIX paths of the regular files really under `folder` (no links), found by walking it."""
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and not p.is_symlink()}


def write_bundle(folder: Path, dest: Path) -> Path:
    """Zip the sealed folder (manifest, SHA256SUMS and every listed file) under `<run>/`. Only files found
    inside the folder go in: a manifest changed on disk can't name a path that leaves it (review of #202),
    such as the evidence key in `<data>/keys/`; an unlisted or missing file is the verifier's to report."""
    manifest = json.loads((folder / MANIFEST).read_text())
    inside = _files_inside(folder)
    root = folder.name
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in (MANIFEST, SUMS, SIGNATURE):
            if name in inside:
                z.write(folder / name, f"{root}/{name}")
        for f in manifest["files"]:
            path = f.get("path") if isinstance(f, dict) else None
            if isinstance(path, str) and path in inside and path not in _NOT_HASHED:
                z.write(folder / path, f"{root}/{path}")
    return dest


# ------------------------------------------------------------------ sealing --
def latest_seal(store: Any, change_id: str) -> dict[str, Any] | None:
    seals = store.events_with_key([change_id], "evidence_seal")
    return seals[-1][2] if seals else None


def verify_seal(data_dir: Path, change_id: str, seal: dict[str, Any] | None) -> Verification:
    """A change's evidence folder checked against its latest seal, signature included."""
    from agent_factory import signing

    return verify(
        Path(data_dir) / "artifacts" / change_id,
        seal.get("sha256") if seal else None,
        seal.get("signed_by") if seal else None,
        signing.trusted_keys(data_dir),
    )


def _pack_facts(cfg: Any, blueprint: str) -> dict[str, Any] | None:
    bp = cfg.blueprints.get(blueprint)
    pack = bp.pack_binding if bp is not None else None
    return pack.evidence() if pack is not None else None


async def seal(mgr: Any, change: Any, product: Any) -> str:
    """Write events.jsonl, the spec snapshot, manifest.json and SHA256SUMS for this
    run as it stands now, and record the manifest's digest in the event log."""
    from agent_factory.artifacts import Ledger
    from agent_factory.models import EventKind

    folder = mgr.ws.data_dir / "artifacts" / change.id
    folder.mkdir(parents=True, exist_ok=True)
    events = mgr.store.list_events(change.id)
    (folder / "events.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in events))
    wt = mgr.ws.change_dir(change.id)
    spec_dir = folder / "spec"
    for name in ("spec.md", "requirements.yaml"):
        src = wt / "docs" / name
        if src.is_file():
            spec_dir.mkdir(exist_ok=True)
            (spec_dir / name).write_bytes(src.read_bytes())
    commit = None
    repo = mgr.ws.product_dir(product.slug)
    if repo.is_dir():
        res = await mgr.ws.git(f"rev-parse --verify -q run/{change.id}", repo)
        commit = next(reversed(res.lines()), None) if res.ok else None
    images = mgr.store.events_with_key([change.id], "image")
    facts = {
        "run": {
            "id": change.id,
            "iteration": change.iteration,
            "status": str(change.status),
            "summary": change.summary,
            "station": change.current_station,
            "change_request": change.change_request,
            "kind": str(change.kind),  # the work item (ADR-0030); added keys keep schema v1
            "source": str(change.source),
            "requested_by": change.requested_by,
            "spec_approved_by": change.spec_approved_by,
            "fix_loops": change.loops,
            "cost_usd": round(change.cost_usd, 4),
            "created_at": change.created_at.isoformat(),
        },
        "order": {
            "id": product.id,
            "title": product.title,
            "product": product.slug,
            "created_by": product.created_by,
            "app_url": product.app_url,
            "repo_url": product.repo_url,
        },
        "workflow": {"id": change.workflow_id, "version": change.workflow_version},
        # the stack pack the product is built with (ADR-0041 §7); added keys keep schema v1
        "pack": _pack_facts(mgr.cfg, product.blueprint),
        "source": {"branch": f"run/{change.id}", "commit": commit},
        "image": images[-1][2] if images else None,
        "events": len(events),
        # the artifact ledger (ADR-0037): ledger.json is hashed with the other files
        "artifacts": Ledger(folder).summary(),
    }
    (folder / PILLAR_INDEX).write_text(json.dumps(build_pillar_index(folder), indent=2) + "\n")
    manifest = build_manifest(folder, facts, datetime.now(UTC))
    digest = write_manifest(folder, manifest)
    from agent_factory import signing

    signed_by = sign_manifest(folder, signing.signer_for(mgr.ws.data_dir))
    t = manifest["totals"]
    mgr.store.add_event(
        change.id,
        EventKind.decision,
        f"evidence sealed: {t['files']} files, manifest sha256 {digest[:12]}, signed by {signed_by}",
        data={
            "evidence_seal": {
                "sha256": digest,
                "files": t["files"],
                "bytes": t["bytes"],
                "status": str(change.status),
                "signed_by": signed_by,
            }
        },
    )
    return digest
