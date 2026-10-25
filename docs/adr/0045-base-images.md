# ADR-0045: Approved base images, a fallback at Build, and factory-wide image risk decisions

**Status:** accepted · 2026-10-25 · builds on [ADR-0042](0042-image-findings.md) (image findings),
[ADR-0041](0041-stack-packs.md) (stack packs), [ADR-0040](0040-product-work-ledger.md) (work ledger) and
[ADR-0026](0026-cluster-target.md) (credentials through the environment)

## Context
A base-image CVE stopped two live runs in a row: libssl3 in `gcr.io/distroless/nodejs22-debian12` on
2026-10-22 (fixed by pulling the latest base, patch `dcf8c71`), and CVE-2026-31789 in the same package
on 2026-10-25, fixed in Debian but not yet shipped by the image's publisher. ADR-0042 routes these
correctly (hold for a person, accept the risk with an expiry), but:

- **every product asks again.** An accepted risk lives on one product's ledger, so the next product
  on the same image holds on the same CVE;
- **nothing tries another image.** The pack names one runtime image, so a person must edit the pack or
  the product's Dockerfile and start an upkeep change;
- **the image itself is slow to patch.** Distroless follows Debian's point releases.

Docker Hardened Images (DHI) became free in December 2025: minimal, non-root, no shell, patched quickly,
with SBOM and provenance. Pulls need a Docker Hub sign-in (`docker login dhi.io`).

## Decision
1. **A pack lists its approved runtime images, in order** (`stack.base_images`): the first is the
   default, the rest are fallbacks. `nextjs-web` 1.3.1: `dhi.io/node:22-alpine3.23` (Docker Hardened),
   then `gcr.io/distroless/nodejs22-debian12:nonroot`. Each entry names the Dockerfile stage that holds
   it (`stage`) and, when its registry needs a sign-in, the login (`login`, a key under the config's
   `registry_logins`). The template's Dockerfile has one stage per approved image, pinned by Node version
   (and by digest once `scripts/pin-base-images` has run), and a global `ARG RUNTIME` that picks the
   stage; Dependabot (`docker` ecosystem) proposes digest updates for each. The runtime stage runs as
   numeric uid 65532 on every image, so the chart's `runAsUser` holds for all of them.
2. **Build falls back automatically.** When the image scan holds only on base-image findings that have
   a fix released (the publisher hasn't shipped it), Build tries the pack's next approved image: it
   rebuilds with `--build-arg RUNTIME=<stage>` (the branch doesn't change, so verify stands), scans it,
   and runs it once (`docker run`, the pack's liveness path must answer) as the runtime compatibility
   check. The first image that passes both is used. The evidence records every image tried and why
   (`artifacts/<change>/base-images.json`), and the product's work ledger gets an upkeep item proposing
   the new default (`pack upkeep: make <image> the default runtime`). If none passes, the change holds for
   review as before. App-dependency findings still go to develop first (a fallback can't fix them).
3. **Base-image risk decisions are factory-wide.** Accepting the risk of a base-image finding on any
   product records it for that image (repository and tag, digest left out) and finding key, with the
   expiry and the reason. Every product built on that image honours it; each product's ledger item shows
   it as covered by the factory decision. A decision closes itself when a scan of that image no longer
   reports the finding, and lapses at its expiry. App-dependency risks stay per product.
4. **Registry sign-in through the environment.** `registry_logins.<name>` holds a username and a secret
   reference (`dhi: {registry: dhi.io, username: env://DOCKERHUB_USERNAME, token: env://DOCKERHUB_TOKEN}`).
   Build logs in with the token on stdin (`docker login --password-stdin`, ADR-0026), only for an image
   that needs it, and logs out in the same step. The credential serves a new capability, `pull-image`,
   granted to the build station type. Without the login, Build skips that image (and says so) and
   starts on the next one; preflight reports the missing credential as degraded, not failed.

## Consequences
- The same CVE in the same image is decided once, not once per product.
- Most base-image holds resolve without a person: the next approved image is tried within the run.
- A Next.js product's image is Alpine-based (musl) by default. The template has no native modules; the
  runtime check catches an app that adds one and falls back to distroless.
- The product's Dockerfile keeps the default first, so later builds go back to it once it is patched.
- Products built from `nextjs-web` ≤ 1.3.0 have one runtime stage and no `RUNTIME` arg: Build has nothing
  to fall back to and holds as before; factory-wide decisions still apply to them.
- A generated product's own CI builds the distroless stage (no registry sign-in in its repo), and
  Dependabot updates only the stages it can reach without one; the factory signs in for dhi.io.
- Digest pinning needs registry access: `scripts/pin-base-images` (run where Docker is signed in)
  resolves and writes the digests; until it runs, the stages are pinned by tag.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §1 Security & governance: minimal, patched base images | Hardened, shell-less runtime images by default, fallbacks approved in the pack, every choice recorded as evidence. |
| §1 Security & governance: risk acceptance with expiry | One factory decision per image and CVE, with a reason and expiry, closed automatically when fixed. |
| §5 Agent engineering: deterministic judging | The image scan and the runtime check decide which image ships; no agent is asked. |
| §7 Reliability | A runtime check (the image starts and answers its liveness path) before an image is used. |
| §7 Security: credentials | The registry token is a secret reference, passed on stdin, used only by the build station, redacted. |
