# ADR-0044: Design decisions, a v0 baseline and modular web products (J1 scope)

**Status:** proposed · 2026-10-23 · trimmed to what J1 ("launch, done well") needs; the rest of the
2026-10-21 draft is deferred until after J5. Extends [ADR-0041](0041-stack-packs.md) (packs carry the
decision catalogue, modules and design system), [ADR-0017](0017-spec-driven-development.md) (the spec
review gate) and [ADR-0040](0040-product-work-ledger.md) (deferred pillar work becomes ledger items).

## Context
Guestbook (2026-10-20) and Book keeping (2026-10-22) were delivered at Level 3 with every requirement
verified live, and neither is a product Saravana would show a client:
- **The architect is told to stay minimal.** `agents/architect.md` says "Keep v0 simple: no auth unless
  specified". Whatever the spec doesn't ask for is left out by design, so protected writes are open.
- **Decisions are invisible.** "Key decisions" are prose in `design.md`. Nobody sees the options or a
  recommendation, and nothing checks that the important questions were asked.
- **No one owns the look.** The Next.js template has a 40-line `globals.css`, no design system, no
  components. The only UI check is axe, which proves accessibility, not quality.
- **Cross-cutting concerns are wired in, not attached.** Logs, metrics and tracing are imported straight
  from `src/lib`; authentication and authorisation don't exist.

The journey's J1 "done when": a new product built from scratch with agreed design choices, auth on
protected writes, a polished responsive UI, published, deployed, Level 3.

## Decision

### 1. A decision sheet in the design station
- **Catalogue per pack.** A pack may ship `decisions.yaml`: the questions every product of its stack
  answers. Each question has an id, a title, a pillar (ADR-0024), options (each with consequences) and
  a hint for choosing. `nextjs-web` ships five:

  | Question | Options |
  |---|---|
  | `authentication` | none (read-only or private network) · local accounts with sessions |
  | `sign-up` | admin creates accounts · open sign-up |
  | `authorisation` | any signed-in user · roles (`admin`, `member`) |
  | `data-store` | Postgres in the chart · bring your own Postgres URL |
  | `look` | theme (neutral, warm, bold) and density (comfortable, compact) |

  A pack without `decisions.yaml` (FastAPI today) skips the sheet, and the design station says so.
- **The architect answers every question** in `docs/decisions.yaml`: the chosen option, the recommended
  option, a one-line rationale naming the pillar, and the requirement ids it serves. **"Other…"** is
  allowed: free text the architect turns into a concrete choice with its consequences, marked `other`.
- **The engine judges the sheet**, not the agent: every catalogue question has exactly one choice and a
  rationale; an option id exists, or the answer is `other` with text. An incomplete sheet fails the
  design station with the missing ids. The engine then renders one short ADR per answer into the
  product's `docs/adr/` (deterministic, so the ADRs always match the sheet).
- **Who answers** is its own lane setting, independent of spec review (decided 2026-10-23):
  `decisions: autonomous | ask`, with `autonomous` the default. Both modes share the review page.
  - **Constraints.** A lane may narrow any question: `decision_constraints: {data-store: [postgres-chart],
    authentication: [local]}` lists the options allowed for that question (one option fixes the answer).
    Publish refuses an option id the product's pack doesn't define.
  - **`autonomous`:** the architect picks within the constraints, taking its recommendation unless the
    recommendation is outside them. "Other…" is not available, since it would leave the approved
    constraints. Each answer is recorded as `decided_by: architect (recommended)` or
    `decided_by: architect (constrained)`. The change continues without waiting.
  - **`ask`:** after design the change waits for a person on the review page, which shows the sheet as a
    poll per question with the recommendation and its rationale preselected. The person keeps or changes
    each answer within the constraints, or writes "Other…", with an optional reason; answers are recorded
    as `decided_by: <member>`. Approve continues.
  - **One gate, one page.** When the spec review gate also applies to this iteration, the person reviews
    the spec and the decisions together and approves once. When only `decisions: ask` applies, the page
    shows the decisions with the spec read-only.
- **Intention → spec → plan → aligned, in one flow** (decided 2026-10-23, PR 1b):
  - **Start from intention or from a spec.** "I have a spec" on the create form still makes the
    customer's spec the specification (wording and numbering kept); the architect plans from it and the
    same conversation follows.
  - **Open questions go to the architect, not back to the requester.** In a lane with a design station,
    intake never stops for questions; it stops only on a stack conflict (a person picks the stack).
    Every question, blocking ones included, goes in `docs/open-questions.yaml` (Q1, Q2, …). The architect proposes an answer to each in the
    sheet's `open_questions`, and the engine checks every one is answered, as it does the design
    questions. They are recorded with the ADRs (`docs/adr/NNNN-open-questions.md`).
  - **Align with the architect** on the review page (whenever the change waits there: `decisions: ask`
    or the spec gate). The person's design choices, their own answers to open questions, and their
    comments or questions are collected as a draft and sent together. The engine saves the choices and
    answers first (judged as a whole, within the lane's constraints), then runs the architect once on
    all of it (`write-docs`, the design docs only; anything else it touches is undone). It replies,
    makes the spec, design and plan consistent, and the change comes back to the page.
  - **Requirements changes go back to the specification.** The architect can't edit the numbered
    requirements or scenarios. When the person's input needs that, it says what must change; the
    engine adds it to the review notes, intake updates the spec, design runs again with the whole
    conversation, and the change returns to the page.
  - **One approval.** Approve builds what was agreed; it is refused while the sheet is incomplete.
    "Request changes" and direct spec edits leave the page: every change goes through the architect.
  - The conversation is stored per change and sealed with the evidence (ADR-0023). Its text is the
    member's input to the design, never instructions to the factory.
- **Later changes** keep the sheet. The architect re-opens only questions the request touches, and the
  review page shows the decision diff next to the spec diff.

### 2. A v0 baseline when the spec is silent
The architect's prompt replaces "no auth unless specified" with:
> Build the smallest useful v0 that meets the stated requirements, with essential security,
> observability and reliability controls working by default. Protected writes need authentication and
> authorisation. Logs, health checks and resource limits are on; metrics and traces where useful.
> Document your assumptions. Add no infrastructure just to fill a pillar: defer it in
> `docs/roadmap.md` with the trigger for adding it.

"Protected writes" means any write that changes data another person relies on. A public read-only page
stays public; a guestbook post may be open if the sheet says so, with its reason.

### 3. Modules in `nextjs-web` (first set only)
- `src/modules/<name>/` holds a **port** (the interface app code calls), its **adapters**, and its
  **contract tests**. App code imports only `@/modules/<name>` (the port). Which adapter runs is set in
  one place, `src/modules/config.ts`, from environment.
- **First set:**

  | Module | Adapters | No-op |
  |---|---|---|
  | `auth` (authentication) | local accounts: scrypt hashes, an httpOnly `SameSite=Lax` session cookie, sessions in Postgres, sign-in and sign-out pages, origin check on mutating route handlers | `none`: every request is anonymous |
  | `authz` (authorisation) | `signed-in` (any signed-in user) or `roles` from the session (`authorize(request, role)`, `authorizePage(path, role)`) | `allow-all` |
  | `logging` | pino JSON to stdout (moved from `src/lib/logger.ts`) | silent |
  | `metrics` | Prometheus (moved from `src/lib/metrics.ts`) | no-op registry |
  | `tracing` | OpenTelemetry (moved from `src/instrumentation.ts`) | off |

- **First account:** when `auth` is on, the chart creates `ADMIN_EMAIL` and a random `ADMIN_PASSWORD` in
  the app's Secret; the app creates that admin on first start if no user exists. The password never
  appears in config, logs or evidence (secret reference, redacted). `make preview` seeds a known test
  admin so the e2e suite can sign in.
- **Hidden scenarios and live e2e sign in** with the deployment's admin account, passed to the verifier
  and to `make e2e` as a secret reference, redacted in transcripts.
  *As built (PR 2):* the pack names the Secret (`stack.admin_secret`: `{release}-admin`, its keys and a
  sign-in how-to for the verifier). After deploy the acceptance station reads it through the deploy
  provider (`read_secret`, `kubectl get secret`, not recorded); every value goes to the redactor, and
  reaches the e2e sandbox and the verifier's session only through the environment, by name
  (`E2E_ADMIN_EMAIL`, `E2E_ADMIN_PASSWORD`; `SandboxSpec.allow_env`, never in argv or the prompt). No
  Secret (an app built before 1.3.0, or a target that can't read Secrets) means signed-out checks.
- **Which adapter runs** follows the sheet: the developer sets `DEFAULTS` in `src/modules/config.ts`
  (`authentication: local` → `auth: "local"`; `authorisation` → `authz: "signed-in" | "roles"`); the
  environment can override each (`AUTH`, `AUTHZ`, `LOGGING`, `METRICS`, `TRACING`), and an impossible
  combination (roles without sign-in) stops the app at start.
- **Two pack signals** (ADR-0041 §3), level 3, counted for this pack's products only:
  - `modules_isolated` (maintainability): `.dependency-cruiser.cjs` forbids imports of
    `src/modules/*/adapters/**` from outside the module, and `make lint` runs it;
  - `module_contracts` (reliability): every module folder has a contract test that runs against each of
    its adapters, the no-op included (`make verify` runs them).
  Products built from an earlier template (`src/lib/logger.ts`, no `src/modules/`) pass both, and the
  moved logs, metrics and tracing signals accept either layout, until an upkeep change adopts modules.
- Resource requests and limits are already in the chart; probes stay as they are.
- **FastAPI** gets modules after J5 (deferred list), so `fastapi-service` is unchanged.

### 4. Look and feel in `nextjs-web`
- **Tailwind CSS v4 and shadcn/ui.** Design tokens (colour, type scale, spacing, radius) for light and
  dark live in `src/app/globals.css` as CSS variables; the `look` answer picks the token set. The template
  carries the shadcn/ui components a CRUD web app needs, copied into `src/components/ui/` (button, input,
  label, textarea, select, card, table, badge, dialog, toast, empty state), plus an app shell (header,
  navigation, account menu, responsive layout). System fonts only: builds have no network to font hosts.
  Another design system is allowed when the sheet records why.
- **Designer.** The design station runs a **designer** agent after the architect (same station, its own
  agent call record, no new station type). It writes `docs/ui.md`: each page, its layout, the components
  it uses, and its empty, loading, error and signed-out states. It loads a new pack skill,
  `nextjs-ui-design`, written for this template (no third-party design skill, so no licence question).
- **Screenshots as evidence.** The e2e suite saves a screenshot of every route in `e2e/routes.ts`, light
  and dark, at 1280 px and 390 px wide, signed in where a route needs it. Preview-e2e and acceptance
  collect them into the change's evidence; the change page shows them.
- **UI review reports only.** The code-review station's reviewer also scores the preview screenshots
  against a written rubric in the pack (hierarchy, spacing, consistency, states, responsiveness, 1–5
  each). The findings are recorded as evidence and shown on the change page; they don't fail the change.
  Blocking waits until the rubric is calibrated on known-good and known-bad screenshots (deferred).

### 5. A pillar roadmap per product
The architect writes `docs/roadmap.md`: one row per quality pillar (ADR-0024) with **now**, **deferred**,
**why** and **trigger** (for example "rate limits: when the app is public and sign-up is open"). The
design station parses it; each deferred row becomes a product work ledger item in category `roadmap`, so
"Make this change" can pick it up later. A malformed roadmap fails the design station like a missing file.

## Settled (Saravana, 2026-10-23)
1. Decision autonomy is its own lane setting (`decisions: autonomous | ask`) with constraints per
   question, sharing the review page with spec review.
2. The designer is a second agent inside the design station, after the architect.
3. Live e2e and hidden scenarios sign in with the deployment's admin account, as a redacted secret
   reference.
4. Discuss with the architect is part of J1 (PR 1).
5. Intention becomes spec, spec becomes plan, and the person aligns with the architect in one flow:
   open questions go to the architect, and everything the person says is collected and sent to it
   together (PR 1b).
6. Full autonomy means no stops for input: in an autonomous lane even blocking questions are answered by
   the architect as stated assumptions. A change waits only when a person's authority is needed: a stack
   conflict, risk approval, accepting a vulnerability, or a failure the factory can't fix. Spec text is
   changed through the architect, not edited directly on the review page.

## Deferred until after J5
Rate limits, caching and feature flags as modules; OIDC; chart modules (HPA, PDB); FastAPI modules and
decision catalogue; UI review blocking delivery; discuss mode at other stations; an automatic upkeep
change bringing earlier products onto modules ("upgradable selections").

## Consequences
- A new web product starts with sign-in on protected writes, roles, a styled responsive UI and visible,
  attributable design decisions, each recorded as an ADR in its own repo.
- The template grows (Tailwind, shadcn/ui components, five modules). No-op adapters keep a read-only v0
  as light as today.
- `nextjs-web` becomes 1.3.0 (PR 2). New products and new changes to existing ones pin it; Guestbook and Book
  keeping keep the template they were built from (adopting modules is a later upkeep change).
- The design station spends one more agent call (the designer) and each discuss turn is one architect
  call. Both show on the product's Spend card under design.
- Auth makes every e2e and hidden scenario sign in first. Tests that pass today may need a sign-in step;
  the verifier prompt and the template's e2e helpers carry it.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §1 Autonomy Maturity Model: code quality | Ports and adapters; `modules_isolated` enforced by lint. |
| §1 Security & governance | Authentication and authorisation on protected writes in every v0 that needs them; the admin password is a secret reference, redacted. |
| §2 DORA: quality internal platform | The golden path now includes design decisions, modules and a design system. |
| §3 OWASP agentic: goal hijack | Discuss threads can only change the product's docs, never factory rules; sealed with the evidence. |
| §5 Agent engineering: deterministic judging | The engine checks the decision sheet and renders the ADRs; the UI rubric reports until calibrated. |
| §7 Usability | Design system, designer, screenshots per route, UI review evidence. |
| §7 Reliability, operability | `module_contracts`; logging, metrics and tracing as modules with no-op adapters. |
| §7 Performance and cost (uncovered) | Deferred with explicit triggers in the product roadmap and the ledger, not left silent. |

## Delivery (~7.5 days, then the J1 live run)
1. **PR 1, decision sheet** (~2.5 days): `decisions.yaml` schema and loader in packs; the lane's
   `decisions` and `decision_constraints` (publish checks); architect prompt (baseline, sheet, roadmap);
   engine completeness and constraint check, ADR rendering; roadmap rows → ledger; the shared review page
   with the poll and "Other…"; the discuss thread; `nextjs-web` catalogue.
1b. **PR 1b, align with the architect** (~1.5 days): intake's open questions go to the architect;
   one batched alignment turn (choices, answers, comments); requirements changes go back to intake;
   one approval on the align panel.
2. **PR 2, modules and auth** (~2 days): the five modules with contract tests, sign-in pages, admin
   bootstrap, the two signals, e2e and verifier sign-in; `nextjs-web` 1.3.0.
3. **PR 3, look and feel** (~1.5 days): Tailwind v4, shadcn/ui components and app shell, tokens by `look`,
   designer agent and `nextjs-ui-design` skill, screenshot evidence, report-only UI review.
4. **J1 live run:** a new Next.js product from scratch, `decisions: ask`, decisions agreed in the poll,
   published and deployed at Level 3. Its sealed evidence is kept as the J1 demo record.
