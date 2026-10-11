# ADR-0036: Agents reach the model through a pluggable gateway, with a virtual key per call

**Status:** accepted · 2026-10-11 · refines [ADR-0005](0005-model-access.md) (model access) and
[ADR-0014](0014-agent-sandbox.md) (agent sandbox)

## Context
Every agent sandbox got the engine's model credential (`CLAUDE_CODE_OAUTH_TOKEN` or
`ANTHROPIC_API_KEY`) and called `api.anthropic.com` itself. ADR-0014 accepted that as the one
secret a sandbox needs and noted "a later step can move it behind a local auth proxy". Three
needs make that step a release blocker: anything other people run must use API keys, not a
personal subscription; a credential that reaches a sandbox can leak through a prompt
injection; and spend is only known after the fact, from the CLI's own report. Model
profiles (another model per agent, local models) also need one place where models are
routed.

## Decision
1. **A gateway seam** (`gateway.py`), chosen in the config under `model_gateway.provider`:
   - `direct` (default): today's behaviour, nothing changes.
   - `litellm`: the LiteLLM proxy, an optional compose service (`COMPOSE_PROFILES=gateway`)
     with its own Postgres for keys, budgets and spend. The factory stays on SQLite.
   A gateway implements `ModelGateway`: `check()` (readiness), `open(scope)` (a session for
   one agent call) and `close(session, cost)` (revoke, report). Another gateway (Portkey,
   Kong, our own proxy) is one class in `gateway.PROVIDERS`; nothing else changes.
2. **A virtual key per agent call**, not per change: minted by the engine with the gateway's
   admin key just before the agent starts, capped (`call_budget_usd`), short-lived
   (`key_minutes`), labelled with change, station and role, and deleted when the call ends,
   in a `finally`, so a cancelled call is revoked too. One call's key can't be reused by the
   next, and its spend is that call's spend. The key is registered with the redactor, so
   it never appears in events, transcripts or evidence; events name it by alias only.
3. **Two credential modes:**
   - `api_key` (default; required for anything others run): the provider key lives only in
     the gateway. The sandbox gets `ANTHROPIC_BASE_URL` and the virtual key as
     `ANTHROPIC_AUTH_TOKEN`; the engine's `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`
     are **withheld** from it (`SandboxSpec.withhold_env`). A leaked key is worth at most
     one call's budget for at most `key_minutes`.
   - `subscription` (own, local use): LiteLLM (≥ 1.81.14) forwards the agent's own Claude
     login per request and never stores it. The login therefore **still reaches the
     sandbox**; the virtual key travels as `x-litellm-api-key` in
     `ANTHROPIC_CUSTOM_HEADERS`, and routing, budgets and metering apply. API keys are
     withheld, since one would replace the login. Spend is priced at API list rates, so
     budgets are API-equivalent caps. Anthropic's terms restrict using a subscription on
     behalf of others, so this mode stays personal.
4. **Network:** behind a gateway, the gateway replaces `api.anthropic.com:443` in the
   sandbox egress allowlist. The egress proxy runs inside dind and can't resolve compose
   service names, so the engine resolves the gateway's address at start and adds an
   egress route (`model-gateway:4000=<ip>:4000`). `make sandbox-check` probes that the
   gateway is allowed and direct model access is denied. Traffic between sandbox, proxy
   and gateway is plain HTTP on the factory's private networks.
5. **Budgets:** the gateway enforces the per-call cap. The engine refuses new calls in a
   change once `change_budget_usd` is reached (the larger of the CLI's reported cost and
   the gateway's spend, summed per change); the change is held, never run without a key.
   No key (gateway down, budget reached) always means the call is refused, never that it
   falls back to direct access.
6. **Evidence:** each agent call's record (`agent_call`) carries `gateway`: provider, key
   alias, the gateway's spend and whether the key was revoked. Gateway spend is written in
   batches and may lag the call by seconds; the CLI's cost stays the primary figure.
7. **Preflight:** behind a gateway, "Model access" is the gateway's readiness (reachable,
   database connected, admin key set), plus the login in subscription mode. The engine
   no longer needs the provider key itself in api_key mode.
8. **Least privilege:** both gateway services drop all capabilities, publish no port and
   never get the factory's `.env`, only their own variables (`test_least_privilege.py`,
   `scripts/privilege-check.sh`). The gateway keeps no prompts or responses
   (`turn_off_message_logging`). The admin key never reaches a sandbox (`LITELLM` is
   in the sandbox's never-forward list).

## Consequences
- The provider credential leaves the sandbox in api_key mode: closes the open
  "model token behind an auth proxy" item from ADR-0014.
- Model profiles, fallbacks between providers and local models (Ollama) become gateway
  configuration, not engine changes. They are the next steps, with a model comparison in
  evaluations to measure them.
- One more pair of containers when the profile is on. The LiteLLM image tag must be
  pinned to a reviewed version (≥ 1.81.14; ≥ 1.105 marks seat-billed calls in its logs).
- To verify on the first live run (not covered by unit tests): the Claude CLI honours
  `HTTP_PROXY` for the plain-HTTP gateway URL; the `anthropic/*` wildcard serves the model
  ids the CLI resolves `opus`/`sonnet`/`haiku` to; the engine's spend read and key delete
  calls match the pinned LiteLLM version's API.
