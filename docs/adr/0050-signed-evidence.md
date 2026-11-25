# ADR-0050: Signed evidence and a verifier anyone can run

**Status:** proposed · 2026-11-25 (phase B, vision G2) · builds on [ADR-0023](0023-evidence-manifest.md) (sealed
evidence) and [ADR-0049](0049-run-maintain-retire.md) (the decommission record)

## Context
ADR-0023 seals every stopped change: each evidence file is hashed into `manifest.json`, and the manifest's digest is
recorded in the factory's database. That catches evidence that changed on the factory. It does not help someone
holding a **copy**: an auditor, a customer or a regulator given `evidence-<change>.zip` can run
`sha256sum -c SHA256SUMS`, but anyone who edits a file can rewrite the sums too. The digest that would catch it is in
a database they can't see. Vision G2 and phase B ask for manifests that are **signed**, and a **verifier** anyone can
run on a downloaded bundle. The phase is done when a real bundle verifies on another machine and a tampered copy fails.

## Decision
1. **Every seal signs its manifest** (`signing.py`, called by `evidence.sign_manifest` from `evidence.seal` and the
   decommission record in `engine/retirement.py`). The signature is `manifest.json.sig` next to the manifest. The
   manifest already holds every file's SHA-256, so signing it covers them all. The signature file is outside the
   hashed set, like `manifest.json` and `SHA256SUMS`.
2. **The format is OpenSSH's SSHSIG with an ed25519 key**, namespace `agent-factory-evidence`. This is the same
   format `git` uses for SSH-signed commits. It was chosen over cosign or Sigstore (ADR-0023's "stronger options")
   because:
   - **It can be checked with a tool already installed nearly everywhere:** `ssh-keygen -Y verify`, on macOS, Linux
     and Windows. That keeps ADR-0023's promise of checking without our tools.
   - **It needs no network, account or transparency log.** That fits a self-hosted and air-gapped first release.
     Sigstore's keyless flow needs an OIDC identity and Rekor. `cosign sign-blob` with a key needs cosign installed
     to verify.
   - The namespace stops a signature made for something else (a git commit, say) from passing as evidence.
3. **The key is the factory's.** It lives in `<data>/keys/evidence/`: `signing_key` (OpenSSH private key, mode 0600,
   folder 0700) and `signing_key.pub`. The engine's own user (uid 10001) makes it at the first seal, never on a read.
   It uses `O_EXCL`, so two seals at once never make two keys. It is in the data folder, so `make backup` carries it
   (encrypted with age, S-17), and a restored factory keeps verifying what it sealed.
   - **Rotation:** `agent-factory evidence-key rotate --confirm` keeps the old public key under `retired/`, so
     evidence sealed before still verifies on the factory.
   - **Showing it:** `agent-factory evidence-key show` and `GET /api/evidence/key` (any signed-in member) give the
     fingerprint, the public key and the `allowed_signers` line.
4. **The seal records who signed it** (`signed_by`, the key's fingerprint, on the `evidence_seal` event, and
   `Retirement.record_signed_by`). The engine's re-check on every read (`evidence.verify`, `verify_seal`) then
   requires a signature that:
   - is present and matches the manifest;
   - is by that key;
   - is by a key the factory trusts (current or retired).

   A signature that is removed, re-made by another key or broken makes the evidence **not intact**, and the evidence
   panel says why. A seal from before this ADR recorded no signer. It is checked by its hashes only and shown as
   "not signed", never as signed.
5. **The verifier is one file, `scripts/verify-evidence.py`.** It uses only the Python 3 standard library, plus
   `ssh-keygen` or the `cryptography` package for the signature (tried in that order). It never imports the factory.
   It reads a zip in place without extracting it, or a folder. It checks:
   - the bundle is one folder, with no path that leaves it, no links and no duplicates;
   - every path the manifest names is a normalised relative path (no absolute path, no empty, `.` or `..` part, no
     backslash), listed once, and checked before anything is read or hashed: the manifest is untrusted too. The
     factory's own download (`write_bundle`) likewise takes only files it finds inside the evidence folder, so a
     manifest changed on disk can't pull in a file from elsewhere, such as the signing key;
   - every listed file is there with its size and SHA-256, and nothing unlisted is;
   - `SHA256SUMS` agrees with the manifest;
   - the signature is valid SSHSIG in our namespace;
   - the signer is a key **the person passed in**: `--key` (a file, an `allowed_signers` file or a key line) or
     `--fingerprint`.

   **A key inside the bundle is never trusted on its own.** An intruder who rewrites a bundle can sign it with their
   own key, so trust has to come from outside it. Without `--key` or `--fingerprint`, the verifier prints the
   signer's fingerprint and exits 3 ("signer not checked"), never 0. Exit codes: 0 verified, 1 failed, 2 can't
   check, 3 signer not checked. An unsigned bundle fails unless `--allow-unsigned` is given (hashes only, said so).
6. **Publish the fingerprint where the factory can't change it**: the team's README, its repo, a ticket. That is the
   anchor that makes a copy checkable. The evidence panel shows the fingerprint, the verifier command and the plain
   `ssh-keygen` command.
7. **`Signer` is the seam for phase D.** A customer KMS signer implements `public_blob`, `sign` and `trusted`.
   SSHSIG also defines `ecdsa-sha2-nistp256`, which every major KMS can sign. Supporting it is phase D's choice. The
   verifier then needs that key type too.

## Consequences
- A downloaded bundle can be checked by someone who trusts only a published fingerprint. Tampering fails it, even
  when the sums are rewritten to agree.
- **The limit, stated plainly:** the private key is on the factory's host. Someone with write access to the data
  folder could sign whatever they like, and the factory would trust it. Signing protects copies, not the host.
  Phase D's customer KMS signer moves the key off the host. Anchoring digests outside the factory (the published
  repo, a transparency log) is still future work.
- Rotating the key leaves bundles already handed out verifying against the old fingerprint. Rotate when the key may
  have leaked, and say so where the fingerprint is published.
- `cryptography` becomes a direct dependency of the engine. It was already in the lockfile through the Claude Agent
  SDK, and no version changes.
- Evidence sealed before this change stays valid and is labelled "not signed". It is signed the next time the change
  stops and is sealed again.

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §1 Security & governance: audit and evidence | Sealed evidence is signed; its signer is recorded; the re-check reports a removed, foreign or broken signature instead of hiding it. |
| §7 Security: fail closed | The verifier passes only with a trust anchor given from outside the bundle; an unsigned bundle fails unless asked; a seal that recorded a signer is not intact without it. |
| §7 Security: evidence | The phase's "Done when" is a test (`tests/test_signing.py`): a real change's bundle verifies with the published key, and eight kinds of tampering fail it, with both `ssh-keygen` and `cryptography`. |
| §3 Supply chain | No new package version; checking needs only a standard tool (`ssh-keygen`) or Python's standard library plus `cryptography`. |
