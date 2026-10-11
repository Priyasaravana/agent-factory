"""Artifact schemas and the per-change artifact ledger (ADR-0037 §2). Pure apart from file I/O
on the change's own evidence folder.

Every artifact type has a versioned JSON Schema in `schemas/artifacts/<type>.v<n>.json`. A schema
covers the artifact's **manifest**: what the engine records about it and the machine-read facts it
takes from its files (prose is checked for presence, by digest). The ledger
(`artifacts/<change>/ledger.json`) lists every artifact a change was given or produced:
`{type, kind, version, ref, station, sha256, manifest}`. It sits in the evidence folder, so the
seal (ADR-0023) covers it. Never in a manifest: holdout text or secret values.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from agent_factory.contract import ARTIFACT_TYPES, VERSIONS, ArtifactRef, parse_ref

LEDGER = "ledger.json"
LEDGER_TYPE = "agent-factory/artifact-ledger/v1"
ENTRY = "entry"  # the station name recorded for a change's entry artifacts


def schema_name(type_: str, version: int) -> str:
    return f"{type_}.v{version}.json"


@cache
def schema(type_: str, version: int) -> dict[str, Any]:
    if type_ not in ARTIFACT_TYPES or version not in VERSIONS[type_]:
        raise KeyError(f"no schema for {type_}@{version}")
    text = resources.files("agent_factory").joinpath("schemas", "artifacts", schema_name(type_, version)).read_text()
    return json.loads(text)


@cache
def _validator(type_: str, version: int) -> Draft202012Validator:
    s = schema(type_, version)
    Draft202012Validator.check_schema(s)
    return Draft202012Validator(s)


def problems(ref: ArtifactRef, manifest: Any) -> list[str]:
    """Why a manifest does not satisfy its type's schema; empty = valid."""
    if ref.type == "report" and isinstance(manifest, dict) and manifest.get("kind") != ref.kind:
        return [f"{ref}: kind is '{manifest.get('kind')}', expected '{ref.kind}'"]
    out = []
    for e in sorted(_validator(ref.type, ref.version).iter_errors(manifest), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in e.path) or "(root)"
        out.append(f"{ref} {where}: {e.message[:300]}")
    return out


def digest(manifest: Any) -> str:
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(body).hexdigest()


def file_ref(root: Path, rel: str) -> dict[str, str] | None:
    """`{path, sha256}` for a file that exists, else None."""
    p = root / rel
    if not p.is_file():
        return None
    return {"path": rel, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


class Ledger:
    """The artifacts a change was given and produced, in order. Append-only; the latest entry of
    a type is the current one (a repair loop gives a new `branch`)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / LEDGER

    def entries(self) -> list[dict[str, Any]]:
        try:
            return list(json.loads(self.path.read_text()).get("entries", []))
        except (OSError, ValueError):
            return []

    def have(self) -> set[ArtifactRef]:
        return {parse_ref(e["artifact"]) for e in self.entries()}

    def latest(self, ref: ArtifactRef) -> dict[str, Any] | None:
        found = [e for e in self.entries() if e["artifact"] == str(ref)]
        return found[-1] if found else None

    def has_entry(self) -> bool:
        return any(e["station"] == ENTRY for e in self.entries())

    def record(self, ref: ArtifactRef, manifest: dict[str, Any], station: str, location: str) -> dict[str, Any]:
        entry = {
            "artifact": str(ref),
            "type": ref.type,
            "kind": ref.kind,
            "version": ref.version,
            "ref": location,
            "station": station,
            "sha256": digest(manifest),
            "at": datetime.now(UTC).isoformat(),
            "manifest": manifest,
        }
        entries = [*self.entries(), entry]
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"_type": LEDGER_TYPE, "entries": entries}, indent=2, default=str) + "\n")
        tmp.replace(self.path)
        return entry

    def summary(self) -> dict[str, Any]:
        """What the evidence manifest records about the ledger."""
        entries = self.entries()
        return {"entries": len(entries), "artifacts": sorted({e["artifact"] for e in entries})}
