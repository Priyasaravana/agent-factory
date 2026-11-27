# Plan: the first open-source release (v0.1.0) and the open core / licensed / SaaS split

Status: release date **decided by Saravana 2026-11-21**: v0.1.0 public by **2026-12-31**. Built by AI, reviewed by
AI; Saravana validates the outcome of each step. SaaS is **design only** in December (decided 2026-11-21). First
paid features in December: SSO (OIDC/SAML), audit export to a SIEM, evidence signed with the customer's KMS, and a
compliance report pack (decided 2026-11-21). How the code is split is **recommended** below (Saravana asked for a suggestion 2026-11-21); he confirms it before
phase C starts. The phases and their "Done when" are in the roadmap's "Next, in order" (A–E).
Vision and goals: [../vision.md](../vision.md).

## How to split the code (proposed 2026-11-21)
Saravana asked for a researched answer between one repo with an enterprise folder (option 1) and three repos
(option 3).

**Recommendation: one public repo for all product code, plus one private repo for running the SaaS.** This revises
the three-repo suggestion made in the same session; the evidence below changed it.

| | One repo, licensed folder (GitLab, PostHog) | Three repos (core, enterprise, cloud) |
|---|---|---|
| Change touching core and a paid feature | One PR, one CI run | Two PRs, versions kept in step |
| Release and security fixes | One tag, both editions built from it | Every fix released twice |
| Paid code visible | Yes: source-available, used only with a subscription | No |
| Licence boundary | CI-enforced: core never imports the licensed folder | Enforced by the repo boundary |
| Fit for one person validating AI-built work | Good | Doubles the review and release load |

- **GitLab** ran CE and EE as two repos, then merged them in 2019. A single security release had needed about 150
  merge requests across the two; the merge took 55 engineers and over 600 merge requests.
  ([GitLab, 2019](https://about.gitlab.com/blog/a-single-codebase-for-gitlab-community-and-enterprise-edition/);
  [GitLab, Feb 2019](https://about.gitlab.com/blog/merging-ce-and-ee-codebases/))
  EE code now lives in an `ee/` directory, injected only when EE runs, and a mirror without it is published.
- **Open Core Ventures** advises one repository for self-hosted and SaaS alike, with the proprietary code in one
  subdirectory, a LICENSE file that maps directories to licences, and DCO rather than a CLA.
  ([OCV handbook](https://handbook.opencoreventures.com/licensing)) It also warns that separate repos cause
  duplicated changes and confusion about where contributions go.
- **Grafana** keeps Enterprise to what is "uniquely valuable to enterprise requirements" (premium integrations,
  synchronised LDAP/SAML, support) "without ever undermining open source Grafana".
  ([Grafana](https://grafana.com/blog/how-we-differentiate-grafana-enterprise-from-open-source-grafana/))
- The repo is **public already**, so a private enterprise repo would protect only code not written yet. For a
  one-person, AI-built project, the cost of option 3 (double PRs and releases) is the larger risk.

### Layout
```
LICENSE                 Apache-2.0, plus a map: everything except licensed/ is Apache-2.0
licensed/               source-available commercial licence (LICENSE inside); production use needs a subscription
  sso/  siem/  kms/  compliance/  licence/   (the key check lives here, never in core)
engine/  web/  packs/   Apache-2.0 core
```
- **The seam:** core exposes extension points (Python entry points `agent_factory.extensions`: auth provider,
  evidence signer, audit sink, report pack, tenancy hooks; UI plugin slots). `licensed/` registers into them.
- **CI guard:** a test fails when anything outside `licensed/` imports from it, or when a file under `licensed/`
  lacks its licence header.
- **Two images from one tag:** `core` (built without `licensed/`) and `enterprise`. Both are signed (cosign), with an
  SBOM.
- **Licence key:** an offline, signed key (ed25519) checked only by `licensed/licence`. Regulated and air-gapped
  buyers can't call an online entitlement service, which is OCV's default; here it would be optional.
- **What's paid (buyer-based):** what one engineer or one team needs is open; what an organisation running many
  teams needs is licensed. **Safety is never licensed:** the policy floor, sandbox, evidence sealing and signing,
  the verifier, MFA and local sign-in stay in core.

### SaaS
- **December:** design only (this page links the design doc when it is written).
- **Product code for SaaS** (tenancy, metering from the gateway meters) lives in the same repo, under `licensed/`.
- **A private `ops` repo** holds only what runs the service: infrastructure as code, tenant provisioning, secret
  references, runbooks, status page. It is deployment, not product.
- **First hosted offer:** one isolated factory per client, run by the consulting practice; multi-tenant
  self-serve is 2027. Agents execute customer code, so tenants never share a factory.

## The repo is already public
- **No LICENSE today:** the code is public but "all rights reserved", so nobody may use it yet.
- **Secrets:** scan the whole history now (gitleaks and trufflehog). Anything found is **rotated first**; rewriting
  history afterwards doesn't un-leak it.
- **Keep the history.** 49 ADRs and the planning trail are the proof of how the factory is built. Transfer the
  repo to the product org under the chosen name (GitHub redirects the old URL).
- **`.agent-factory/config.yaml` is tracked** (the shipped defaults) while Saravana keeps local-only edits in it.
  Split it into the tracked default and a gitignored local override before release, so a local edit can never be
  pushed.
- **Personal email:** 4 commits carry a personal address. It stays in history unless rewritten; a `.mailmap` maps
  it for display.
- **Turn on:** secret-scanning push protection, branch protection, required checks, CODEOWNERS, Dependabot.

## Release blockers that aren't features
1. **Licence audit:** may images bundle the Claude Agent SDK and CLI (Anthropic's commercial terms)? Plus every
   bundled image, template and imported skill. Record the result in `THIRD_PARTY.md`.
2. **Name and trademark check** before the first public tag.
3. **DCO** for contributions (core stays Apache-2.0; no CLA needed).
4. **Release hygiene:** SECURITY.md, CONTRIBUTING, CODE_OF_CONDUCT, a trademark policy, install by compose and
   Helm, and the README rewritten to the vision.

## December plan
Planned as complete phases at AI build speed (decided 2026-11-21); the roadmap's "Next, in order" holds each phase's
"Done when", and is the one to follow if the two disagree.

| Phase | Target | Done when (Saravana validates each) |
|---|---|---|
| A. Close out the journey and harden | 26 Nov | J4/J5 evidence recorded; baselines; incident slice 0 restores drift and crash in < 5 min, live; S-05, S-06, S-07, S-08, S-10, S-11 closed; Dependabot roll-up; history secrets scan |
| B. Evidence anyone can verify | 29 Nov | Manifests signed; the verifier passes a real bundle and fails a tampered one; config split |
| C. Open core seam | 4 Dec | LICENSE, `licensed/` with the CI guard, extension seam, licence key, core and enterprise images from one tag. Decided 2026-11-26: logs, metrics and traces on open standards for the factory and both packs, slices O1–O6 of [telemetry-standards.md](telemetry-standards.md) (about 20.5 h AI build, 6 h checks: redaction boundary, a tenant boundary (trusted factory and product ids, backend-enforced isolation), both collector modes, engine-owned egress with readback and a live enforcement check, production support per adapter once proven live, the setup card and a hop-by-hop Test connection), with O7 validated live |
| D. First paid features | 14 Dec | SSO, SIEM export, customer KMS signing, compliance pack on the enterprise image only; the factory UI simple by default, in core ([ui-redesign.md](ui-redesign.md)) |
| E. Release | rehearsal 20 Dec, public 31 Dec | Licence audit, name, DCO, docs; rehearsal from public images verified; **v0.1.0 public**; SaaS design doc |

## Risks
- **Four paid features in 3 weeks.** SSO and KMS signing build on core work (sign-in, signing); SIEM export builds on
  the operation records. The compliance pack is the first to slip to January if a week is lost.
- **Saravana's time is the bottleneck.** Every live run needs his Mac and hours of attention.
- **AI reviewing AI is not independent.** The release gate is the engine's and CI's checks, plus Saravana's
  validation of sealed live runs.
