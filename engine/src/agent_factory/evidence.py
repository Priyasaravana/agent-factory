"""Evidence manifest (ADR-0023): one sealed, verifiable record of what a run produced.

Whenever a run stops (delivered, failed, held, waiting on a person, cancelled) the
engine **seals** its evidence folder `artifacts/<run>/`:

1. `events.jsonl`: the run's full event log, exactly as stored (already redacted).
2. `spec/`: the spec the run worked to (`docs/spec.md`, `docs/requirements.yaml`),
   so the bundle shows what was promised next to what was proven.
3. `manifest.json`: run, order, workflow version, commit, image, outcome, and every
   file with its SHA-256 and size.
4. `SHA256SUMS`: the same hashes in coreutils format, so anyone can check a
   downloaded bundle with `sha256sum -c SHA256SUMS`, without our tools.

The manifest's own SHA-256 is recorded as a decision event in the database. On
every read the engine re-hashes the files and compares (`verify`, a pure function
of the folder): a changed, missing or added file is reported, never hidden.

Scope, stated honestly: this detects evidence that changed after it was sealed
(by accident, a bug, or casual editing). Someone with write access to both the
data folder and the database could re-seal; signing and external anchoring are
future work (see the ADR).

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
SCHEMA = "agent-factory/evidence-manifest/v1"
_NOT_HASHED = {MANIFEST, SUMS}  # written last; they describe the others


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


def write_manifest(folder: Path, manifest: dict[str, Any]) -> str:
    """Write manifest.json and SHA256SUMS; return the manifest's SHA-256."""
    body = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    (folder / MANIFEST).write_bytes(body)
    sums = [f"{f['sha256']}  {f['path']}" for f in manifest["files"]]
    sums.append(f"{hashlib.sha256(body).hexdigest()}  {MANIFEST}")
    (folder / SUMS).write_text("\n".join(sums) + "\n")
    return hashlib.sha256(body).hexdigest()


@dataclass
class Verification:
    sealed: bool
    manifest_ok: bool = False  # manifest.json matches the digest recorded in the database
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)  # written after the seal

    @property
    def intact(self) -> bool:
        return self.sealed and self.manifest_ok and not (self.changed or self.missing or self.added)


def verify(folder: Path, recorded_sha256: str | None) -> Verification:
    """Re-hash the folder against its manifest and the manifest against the recorded digest."""
    mpath = folder / MANIFEST
    if not recorded_sha256 or not mpath.is_file():
        return Verification(sealed=False)
    v = Verification(sealed=True, manifest_ok=sha256_file(mpath) == recorded_sha256)
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


def write_bundle(folder: Path, dest: Path) -> Path:
    """Zip the sealed folder (manifest, SHA256SUMS and every listed file) under `<run>/`."""
    manifest = json.loads((folder / MANIFEST).read_text())
    root = folder.name
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in (MANIFEST, SUMS):
            z.write(folder / name, f"{root}/{name}")
        for f in manifest["files"]:
            p = folder / f["path"]
            if p.is_file():
                z.write(p, f"{root}/{f['path']}")
    return dest


# ------------------------------------------------------------------ sealing --
def latest_seal(store: Any, run_id: str) -> dict[str, Any] | None:
    seals = store.events_with_key([run_id], "evidence_seal")
    return seals[-1][2] if seals else None


async def seal(mgr: Any, run: Any, order: Any) -> str:
    """Write events.jsonl, the spec snapshot, manifest.json and SHA256SUMS for this
    run as it stands now, and record the manifest's digest in the event log."""
    from agent_factory.models import EventKind

    folder = mgr.ws.data_dir / "artifacts" / run.id
    folder.mkdir(parents=True, exist_ok=True)
    events = mgr.store.list_events(run.id)
    (folder / "events.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in events))
    wt = mgr.ws.run_dir(run.id)
    spec_dir = folder / "spec"
    for name in ("spec.md", "requirements.yaml"):
        src = wt / "docs" / name
        if src.is_file():
            spec_dir.mkdir(exist_ok=True)
            (spec_dir / name).write_bytes(src.read_bytes())
    commit = None
    repo = mgr.ws.product_dir(order.product_slug)
    if repo.is_dir():
        res = await mgr.ws.git(f"rev-parse --verify -q run/{run.id}", repo)
        commit = res.output.strip().splitlines()[-1] if res.ok and res.output.strip() else None
    images = mgr.store.events_with_key([run.id], "image")
    facts = {
        "run": {
            "id": run.id,
            "iteration": run.iteration,
            "status": str(run.status),
            "summary": run.summary,
            "station": run.current_station,
            "change_request": run.change_request,
            "spec_approved_by": run.spec_approved_by,
            "fix_loops": run.loops,
            "cost_usd": round(run.cost_usd, 4),
            "created_at": run.created_at.isoformat(),
        },
        "order": {
            "id": order.id,
            "title": order.title,
            "product": order.product_slug,
            "created_by": order.created_by,
            "app_url": order.app_url,
            "repo_url": order.repo_url,
        },
        "workflow": {"id": run.workflow_id, "version": run.workflow_version},
        "source": {"branch": f"run/{run.id}", "commit": commit},
        "image": images[-1][2] if images else None,
        "events": len(events),
    }
    manifest = build_manifest(folder, facts, datetime.now(UTC))
    digest = write_manifest(folder, manifest)
    t = manifest["totals"]
    mgr.store.add_event(
        run.id,
        EventKind.decision,
        f"evidence sealed: {t['files']} files, manifest sha256 {digest[:12]}",
        data={"evidence_seal": {"sha256": digest, "files": t["files"], "bytes": t["bytes"], "status": str(run.status)}},
    )
    return digest
