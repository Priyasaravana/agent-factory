# ADR-0023: Evidence manifest: every stopped run is sealed, re-verified on read, downloadable

**Status:** accepted · 2026-10-01

## Context
A run already produces good evidence, but it was scattered and unchecked:
- the event log is in the database;
- review, traceability, SBOM, provenance and transcripts are files under
  `artifacts/<run>/`;
- the spec is in the product repo.

So three questions had no answer:
- **"Show me everything for this delivery."** Nobody could hand an auditor,
  customer or reviewer one package.
- **"Is this what the factory produced?"** Nothing could tell whether an evidence
  file had changed after the run, through a bug, a cleanup script or an edit.
- **"What was promised, and what was proven?"** The spec and the proof lived
  in different places.

## Decision
1. **Seal the run's evidence whenever it stops.** That means delivered (after
   the retro), failed, held, waiting on a person, paused, or cancelled
   (`evidence.seal`). Sealing never fails a run; a failed seal is logged. The
   engine writes into `artifacts/<run>/`:
   - `events.jsonl`: the full event log as stored (already redacted);
   - `spec/spec.md` and `spec/requirements.yaml`: the spec the run worked to;
   - `manifest.json`:
     - the run (status, summary, fix loops, cost, change request, spec approver);
     - the order (creator, app and repo URL);
     - the workflow version and the run branch's commit;
     - the image;
     - **every file with its SHA-256 and size**;
   - `SHA256SUMS`: the same hashes in coreutils format.

   Post-run work (the retro, the seal) never blocks the next iteration: feedback
   is accepted as soon as a run has stopped.

   A later stop (for example after a hold is resumed) seals again. Each seal is
   a decision event in the database carrying the manifest's SHA-256
   (`evidence_seal`).
2. **Re-verify on every read.** `evidence.verify` is a pure function of the folder
   and the recorded digest. It reports:
   - a manifest that no longer matches its recorded digest;
   - **changed**, **missing** and **added** files.

   Nothing is hidden or "repaired".
3. **API and UI.**
   - `GET /api/runs/{id}/evidence` returns the file list, the hashes and the
     verification result, for every signed-in member.
   - `GET /api/runs/{id}/evidence/bundle` returns a zip of the sealed folder. It
     includes transcripts (full tool output), so it is for the **order's creator
     or an admin**, like transcripts (ADR-0022).
   - The run page has an **Evidence** panel: intact or changed, the manifest
     digest, the commit, the files, and a download button.
   - Anyone can check a bundle with `sha256sum -c SHA256SUMS`, without our tools.
4. **Never in a bundle:**
   - the hidden scenarios (stored outside the artifacts folder);
   - secret values (every stored text goes through REDACTOR).

## Consequences
- One download answers "what was asked, what was built, how it was checked,
  what every agent did, and what it cost" for any run.
- Changed evidence is visible on the run page and in the API.
- **The limit, stated plainly:** this is tamper-*evidence* against changes to
  the files. It is not a defence against someone who can write both the data
  folder and the database, because they could seal again. Stronger options are
  not done yet:
  - signing manifests (Sigstore/cosign, with the registry provider);
  - anchoring digests outside the factory, such as in the published repo or a
    transparency log.
- Disk: small. The event log copy and the manifest sit next to files already
  kept, and are covered by `make backup`.
- Runs from before this change have no seal. Their evidence panel stays hidden
  until they stop again.
