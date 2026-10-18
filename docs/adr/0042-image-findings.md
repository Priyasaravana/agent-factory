# ADR-0042: Image findings as evidence, routed by type

**Status:** accepted · 2026-10-18 · builds on [ADR-0040](0040-product-work-ledger.md) (work ledger),
[ADR-0023](0023-evidence-manifest.md) (sealed evidence) and [ADR-0041](0041-stack-packs.md) (stack packs)

## Context
The first live Next.js product (Guestbook) was held after three loops. Trivy refused every image
because the `node:22-bookworm-slim` base carried Debian's `perl-base` with three fixable CRITICAL
CVEs. The Build station saw only Trivy's exit code, so it sent the developer agent back to fix
the "image" each time. The developer can't change a base image's OS packages, and nothing told it
which package or version was at fault. Patch 0038 moved the pack to distroless, but the next
base-image CVE would loop the same way.

The scan command also used `--ignore-unfixed`, so a CRITICAL CVE with no fix released passed
without a trace.

## Decision
1. **Read the report, not the exit code.** `scan_command` outputs Trivy's JSON
   (`--format json --severity HIGH,CRITICAL`). The provider captures the whole report, and the
   pure `image_scan.parse` turns it into findings: CVE, package, installed version, fixed version
   (empty when none is released), severity and origin. The raw report is never kept; only
   normalised findings go into `artifacts/<change>/image-scan.json`, which is sealed with the change.
2. **Origin decides who can fix it.**
   - **App dependency:** a language package the product's committed lockfile pins
     (`package-lock.json`, `uv.lock`, `poetry.lock`).
   - **Base image:** every OS package, and every language package the product doesn't pin (npm
     inside the Node.js image, pip inside the Python image). Without a lockfile, a language package
     under `usr/`, `opt/` and similar system paths is the base image's.
3. **Routing at the blocking severity** (`scan_fail_on`, default `critical`), by the pure
   `image_scan.judge`:

   | Finding | Build does | Why |
   |---|---|---|
   | app dependency, fix released | fails to develop with the exact bump (`next: 15.1.0 → 15.1.8`) and how to bump a transitive one | the developer can fix it |
   | base image, fix released | **holds for a person at once**, naming the base image | only a new base image (pack version or the product's Dockerfile) fixes it |
   | no fix released | **holds until a person accepts the risk** with an expiry | nobody can fix it today; it must not pass silently |
   | the same app findings after a fix loop | holds instead of looping again | another loop won't help |
   | risk accepted and not expired | passes, counted as accepted | a person decided |

   When a person is needed, the hold also lists the app bumps the developer gets after the person
   resumes.
4. **High findings are recorded, never blocking.** They land in the product's work ledger as open
   items.
5. **Findings live in the work ledger** (ADR-0040) as `issue` items with category `image`, folded
   in at every scan (`ledger.fold_image_scan`), not only when the change stops. That way a person
   can accept a held change's risk on the product page and resume it.
   - The scan checks the whole image, so an item it no longer finds is **fixed**, and one found
     again **reappears**.
   - An accepted risk past its date opens again and blocks the next build.
   - Assessments never close image items: they don't scan images.
   - Evaluation products keep no ledger (ADR-0025).
6. **Older configs keep working.** A report that isn't Trivy JSON is said in the change's events,
   and the command's exit code decides, as before.

## Consequences
- A base-image CVE costs one hold, not three developer loops and their spend.
- Every blocking or high CVE is visible on the product page with its history, and every risk
  acceptance has a person, a reason and an expiry.
- Unfixed criticals now stop delivery until someone decides. That is stricter than before, by
  choice: an unpatched critical CVE in a shipped image needs a named owner.
- Installs with an edited `scan_command` keep the exit-code behaviour until they switch to JSON.
- Scheduled rescans of live images and the upkeep change that rebuilds on a patched base stay in
  the backlog ("Upgradable selections").

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §2 DORA: change failure rate, cost per delivered change | Base-image and unfixed CVEs hold at once instead of spending fix loops the developer can't win. |
| §3 OWASP agentic: cascading failures | The engine judges the findings and routes them; a loop that changes nothing holds. |
| §3 OWASP agentic: human-agent trust | Accepting a risk is a person's decision with a reason and an expiry, kept in the ledger's history. |
| §4 Supply chain | Image CVEs are evidence per change (`image-scan.json`), sealed with the SBOM and provenance. |
| §7 Quality pillars: security | Image findings carry severity and origin; accepted risks are visible, not skipped. |
