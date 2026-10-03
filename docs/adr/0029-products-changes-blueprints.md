# ADR-0029: Products, changes and blueprints

**Status:** accepted · 2026-10-03 · follows [ADR-0028](0028-station-names-and-phases.md) (stations named after the DevOps loop)

## Context
The core nouns came from the first prototype, where "a customer places an order on a
production line" was the metaphor:

- an **order** was a request to build a new app, but it lived on as that app: it
  owned the repo, the URL and every later iteration;
- a **run** was one iteration of that app, which CI tools also call a run, so "the
  run failed" was ambiguous;
- a **product line** was how a kind of app is built and where it runs.

The next steps (existing repos, ticket intake, work items) need words that still make
sense when the work is "fix a bug in this repo" rather than "build me an app". An open
source release also needs names a newcomer reads correctly in the code.

## Decision
1. **Three names, everywhere: UI, API, config, code, database.**

   | Before | Now | Meaning |
   |---|---|---|
   | Order | **Product** | one app the factory builds and maintains: its repo, URL and history |
   | Run | **Change** | one iteration of a product: the stations from request to handover |
   | Product line | **Blueprint** | how a kind of app is built and where it runs: stack, template, workflow, environment |

   Code follows: `Product`, `Change`, `ChangeStatus`, `ChangeManager`, `Blueprint`,
   `product_id`, `change_id`, `slug`, `blueprint`; API `/api/products`, `/api/changes`;
   UI `/products/<id>`; config `blueprints:`; preflight gate kinds `product` and `change`.
2. **Upgrades keep every record.** On first start the SQLite store renames
   `orders`/`runs`/`run_transitions` to `products`/`changes`/`change_transitions`
   and the `order_id`/`run_id` columns, in place. Stored documents and config with
   the old field names still load (Pydantic aliases): `product_lines:`,
   `max_concurrent_runs`, `max_loops_per_run`, `run_wall_clock_minutes`, and the
   `order_id`/`run_id` fields agents may still send to `log_decision`. New writes
   use the new names. Old UI links `/orders/<id>` redirect.
3. **Some technical names stay**, because they are formats or addresses rather than words people read:
   - the data folders `runs/<id>/` and `artifacts/<id>/`;
   - git branches `run/<id>` in product repos;
   - the container label `af.run`;
   - the sealed evidence manifest (`"run"`, `"order"` keys; schema v1, ADR-0023);
   - event data keys;
   - `EvalRun` (an evaluation's run of its suite);
   - the `learn_from_runs` setting.
4. **Work items come next** on this vocabulary: a request is a *change* to a *product*
   (new, feature, bug, upkeep, assess, remove), whether the product was built here or
   is an existing repo.

## Consequences
- The UI, API and code say the same thing, and the words survive the move to existing
  repos and tickets.
- One large, mechanical change (about 1,400 lines in the engine, plus UI and docs). It was
  made with a token-level renamer that only touched code names, then string keys, SQL and
  messages by a reviewed list; the full test suite plus a migration test of a pre-rename
  database guard it.
- API clients of `/api/orders` and `/api/runs` must move to the new paths. There were no
  external clients yet, so no aliases are kept for the API.
- Historical ADRs keep their original words.
