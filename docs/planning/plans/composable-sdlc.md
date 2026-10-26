# Plan: composable SDLC (the factory as a configurable lifecycle)

Status: direction agreed with Saravana 2026-10-11. **Step 1 is built** (ADR-0037, amended by ADR-0039:
lanes, phase registry, capability boundaries, the builder); 1d (lane rebase) and steps 2–5 are deferred until
after J5 ([roadmap](../roadmap.md)). Where this page and an ADR disagree, the ADR wins.

## Why
Today a workflow is tied to a blueprint (fastapi-service, existing-repo) and assumes its output is code in a repo.
Orgs adopt tools one phase at a time: product owners want only design and tickets, a dev team only develop, others
test, security, delivery or observe, and some the whole package. The factory should serve all of them, for any stack,
with a person able to own each step.

## Decisions (2026-10-11)
1. **One station library, one factory standard, presets cut from it (option 1).**
   - Station library: every station belongs to a phase (ADR-0037 catalogue: discover → design → plan → develop →
     test ⇄ secure → deliver → operate).
   - **Factory standard** = the full lane with our quality bar ("full package"; first focus for demos and clients).
   - **Presets** = saved subsets of the standard: Design only, Dev only, Test + Security, Delivery, Observe, Full.
     A preset is a starting point: an org may add, remove and reorder stations freely.
   - **Policy floor**, enforced by the engine and not removable by an admin: a lane that changes code keeps its tests
     and the change-risk check. A lane that changes no code (e.g. Design only) has no floor.
2. **Every station has a human mode**: autonomous · notify · approve (approve / send back) · discuss (chat with the
   agent about the input or output, and edit it directly, before approving).
   **Default in the factory standard:** autonomous with notify everywhere, **approve** at design, change-risk and release.
3. **Station owners are roles mapped to people per product**: product owner, tech lead, security, QA (more as needed).
   Stations name a role; each product maps roles to members. (Refined by ADR-0037 Q4: creator defaults to
   product-owner only; other roles assigned explicitly.)
4. **A workflow can start at any phase**, from: a ticket (Jira / GitHub Issue), a spec or design doc, a repo + branch,
   or a built image or URL.
5. **Any stack.** The stack comes from stack packs (build, test, run, deploy commands per stack), not from the
   workflow. FastAPI became pack #1 (ADR-0041).

## What step 2 onwards still needs
| Piece | Today | Needed |
|---|---|---|
| Human per station | one-off gates (spec review, decisions/align, risk approval, feedback gate) | `owner` role + `mode` on every station; one generic "awaiting owner" state; discuss = chat thread per station run; separation-of-duty rules as data |
| Design phase | requirements/design stations as a prelude to code | product thinking, stories with acceptance criteria, export to Jira / GitHub Issues (connector) |
| Entry points | new product (text) or repo URL | start from ticket, doc, repo+branch, image/URL; intake validates the input against its schema |
| Presets | presets per lane (1c) | lane rebase from the standard (1d) |

## Build order (remaining)
1. **1d** lane rebase from the standard (~1 day).
2. **Human mode + owner roles + separation of duty** on every station (absorbs the one-off gates; category 6
   removal confirmation becomes "owner approves a removal list" on a station).
3. **Start at any phase**: intake from ticket / doc / repo+branch / image-URL; handlers read through the ledger.
4. **Design-phase pack**: product thinking, stories, Jira / GitHub Issues export.
Then: secret store ([secret-store.md](secret-store.md)), category 6, GitHub App, CI results back, Issues intake,
model profiles.
