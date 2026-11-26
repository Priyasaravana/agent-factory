# ADR-0051: The open core seam: one repo, a licensed folder, extension points and an offline licence key

**Status:** proposed · 2026-11-26 (phase C) · split confirmed by Saravana 2026-11-26 (one repo with `licensed/`, a
custom commercial licence for it), as recommended in [plans/open-source-release.md](../planning/plans/open-source-release.md)

## Context
Vision and the release plan split the factory into an Apache-2.0 core, a licensed (paid) edition and, later, SaaS.
The repo is already public with no LICENSE, so nobody may use it yet. Phase C must make the split real before the
first paid features (phase D) are written: a licence on every file, a boundary CI enforces, a way for paid code to
plug in without the core depending on it, a licence check that works offline, and two images from one tag. The phase
is done when the core image builds and passes CI without `licensed/`, and the enterprise image loads an extension
with a valid key and refuses it with an invalid one.

## Decision
1. **One repo, two licences** (Saravana, 2026-11-26). The root `LICENSE` maps directories: `licensed/` is under the
   Agent Software Factory Commercial Licence (`licensed/LICENSE`, a draft for a lawyer to review before phase E:
   source-available; development use free; production use of a feature needs a subscription that covers it; no
   bypassing the key; no hosted resale), everything else under Apache-2.0. Chosen over Elastic License 2.0 and BSL
   because the paid edition is sold per feature to organisations self-hosting, which those licences don't express.
   `licensed/NOTICE` names the Licensor; the legal entity is named before release (phase E).
2. **The core never imports `licensed/`.** `tests/test_licence_boundary.py` fails when code outside it imports
   `agent_factory_licensed` or reaches into `licensed/`, when the engine or auth package depends on it, when a source
   file in it lacks `SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial`, or when the factory image's core
   stage copies it. CI also builds the core image from a checkout with `licensed/` deleted.
3. **Extension points, not hooks** (`engine/src/agent_factory/extensions.py`). The core looks for Python entry points
   in the group `agent_factory.extensions` and lets each register into a `Registry` at five named points:
   `evidence_signer` (a customer KMS, phase D), `audit_sink` (SIEM export), `report_pack` (compliance pack),
   `tenancy` (SaaS) and `ui_slot`. Registration is all or nothing per feature; a single point (`evidence_signer`,
   `tenancy`) is provided once and a second provider is refused; an unknown point refuses the feature. An extension
   that raises is recorded as `failed`, one that declines as `refused`, and the factory starts on core behaviour
   for it. The core calls the points where they belong: `signing.signer_for` uses an extension's signer when there
   is one (verifying is unchanged: `signing.check` against the signer's trusted keys), and the state store emits
   `change.created` and `change.status` audit events (ids, statuses, who asked; outside its lock; a broken sink is
   logged and never stops a change).
4. **Safety is never an extension point.** The policy floor, sandbox, guardrails, evidence sealing and signing, the
   verifier, local sign-in and MFA stay in core; no point can switch one off (`test_safety_is_not_an_extension_point`).
5. **An offline licence key, checked only in `licensed/`** (`agent_factory_licensed.licence`): one line,
   `AFL1.<payload>.<signature>` (base64url), the payload JSON (`id`, `licensee`, `features` from sso | siem | kms |
   compliance, `installations`, `issued`, `expires`), signed ed25519 by an issuer key listed in
   `licensed/…/licence/issuers.txt`. No network, so regulated and air-gapped buyers can use it. Every unreadable,
   altered or untrusted key is refused; a key warns 30 days before `expires`, keeps working 14 days after it (and
   says so), then is refused. Refused means the licensed features don't load; core, local sign-in included, keeps
   working, so an expired key never locks anyone out. The key is a mounted file (`.secrets/agent_factory_licence`,
   S-06), or `AGENT_FACTORY_LICENCE`. The module uses only the standard library and `cryptography`, so the auth
   service can check the same key for SSO (phase D) without importing the engine. `issuers.txt` ships empty: until
   the Licensor runs `make licence-issuer` (private key outside the repo) and commits the public line, every key is
   refused. Adding an issuer is reviewed like code.
6. **The licensed edition is one entry point** (`agent_factory_licensed:extension`). It checks the key once, sets the
   edition to `enterprise` with the licence's summary (holder, features, dates; never key material), and registers
   each feature the key covers; a feature this build has that the key doesn't cover is recorded as refused, with the
   reason. Phase C's first feature is `siem`'s file sink: a hash-chained JSON-lines audit log per UTC day
   (`$DATA_DIR/audit`, mode 0600) that a SIEM agent tails; `verify_chain` finds a removed or edited line. Phase D
   adds direct delivery, SSO, KMS signing and the compliance pack at the same points.
7. **Two images from one Dockerfile and one tag.** `images/factory/Dockerfile` has a `core` stage (the default
   target: a plain `docker build` never ships licensed code) and an `enterprise` stage (core plus `licensed/`,
   installed `--no-deps`: it adds no dependency beyond core's locked set). Compose builds `${FACTORY_EDITION:-core}`.
   `.github/workflows/release.yml`, on a `v*` tag, builds both, proves each is its edition (`agent-factory edition
   --require`), pushes them to GHCR, makes an SPDX SBOM of each (syft) and signs both keylessly with cosign, with the
   SBOM as a signed attestation. The auth, web and sandbox images join it in phase E.
8. **What the UI shows.** `GET /api/edition` and Settings → Readiness & integrations → Edition: which edition runs,
   the licence's holder, features and expiry (with its warning or grace), and each feature's status and reason. The
   key is never entered or shown in the UI.

## Consequences
- The repo can be given its licence and published as open core; contributors know which licence a file is under.
- Phase D builds each paid feature in `licensed/` against a point that already exists; a feature that needs a new
  point adds it to `extensions.POINTS` in core first, in its own reviewed change.
- The paid code is visible (source-available). The licence, not obscurity, protects it: someone can edit the check
  in their copy, which the licence forbids.
- **Not done here:** the lawyer's review of `licensed/LICENSE` and the Licensor's name (phase E); the first issuer
  key (Saravana, on his machine); signed images for auth, web and the sandbox (phase E); the UI plugin slots render
  nothing yet (no licensed UI until SSO).

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §4 Supply chain | Both editions are built from one tag, given an SBOM and signed (cosign keyless) with the SBOM attested; the enterprise layer adds no dependency beyond the core's locked, hash-checked set (S-07). |
| §6 Enterprise governance | SIEM export starts with a tamper-evident, hash-chained audit log of every change's status; the licence summary says what an install may use. |
| §7 Security: fail closed | A key that can't be read in full, is altered, untrusted or past grace is refused; an empty issuer list trusts nothing; a broken extension loads nothing and core runs on. |
| §7 Security: least privilege | Extensions plug in only at named points; safety is not one of them; a single point can't be taken over by a second provider. |
| §7 Maintainability | One repo and one release instead of three (GitLab's 2019 merge, OCV's advice); the boundary is a test, not a convention. |
