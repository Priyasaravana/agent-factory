# ADR-0014: Agents and agent-written code run in throw-away sandboxes

**Status:** accepted · 2026-09-29 · design: [integrations.md §5](../design/integrations.md) · threat model: [security/threat-model.md](../security/threat-model.md)

## Context
Until now the Claude CLI, and every Bash command an agent ran, executed inside
the factory container. That container holds:
- `.env` (model token, `GITHUB_TOKEN`, other secrets);
- the dind TLS client keys (full control of Docker, and so of the kind cluster);
- the kubeconfig;
- the state database;
- a route to the engine on `127.0.0.1:8000`, where gateway identity headers can be forged.

The guardrail hooks are a denylist. A heredoc, an interpreter one-liner or an
indirect path gets around them. The Verify station also ran the generated app's
own tests (code written by agents) in the same container, with the full
environment.

## Decision
1. **One sandbox per agent session and per untrusted command.** The engine
   points the Agent SDK at `agent-factory-sandbox-claude` (`cli_path`). That
   wrapper replaces itself with `docker run -i … claude <same args>`.
   - The SDK's stdin/stdout stream is unchanged, so hooks, the in-process MCP
     tools and structured output keep working.
   - Verify runs `make verify` in a fresh sandbox the same way (`SandboxExecutor`).
2. **What a sandbox gets:**
   - the run worktree (read-write);
   - the product's git metadata (read-only), plus that worktree's own index;
   - the pinned imported skills (read-only);
   - tmpfs `/tmp` and `$HOME`, and a package-cache volume.
3. **What a sandbox never gets:**
   - no Docker socket, TLS keys or kubeconfig;
   - no holdout folder;
   - no state database;
   - no factory environment except the model credential and SDK variables.
     Secrets are inherited by name, never put on the command line.
4. **Hardening:**
   - non-root uid 10001 and a read-only root filesystem;
   - `--cap-drop ALL` and `no-new-privileges`;
   - pids, memory and CPU limits;
   - `--init`, and removal after the session (also on cancel, timeout and restart).
5. **Network:** sandboxes join the `--internal` network `factory-sandbox`, which
   has no route out. The only exit is `factory-egress`, a small allowlist proxy
   that also sits on the default bridge.
   - It allows `CONNECT host:443` and plain-HTTP requests only to
     `sandbox.egress` entries (model API, PyPI, and apps on `dind:8081-8085`
     for acceptance).
   - It logs every allow and deny decision as JSON.
   - TLS is never terminated.
6. **Privileged work stays in the engine:**
   - docker build, image scan, SBOM, `kind load`, helm, cluster diagnostics,
     git commit, merge and publish.
   - Deploy repair works from the diagnostics the engine collects; agents have
     no cluster access.
7. **The image is built inside dind by the engine on first start.** It contains
   the Claude CLI the SDK bundles (same version), the factory plugin, uv and git.
   - Its tag is a hash of its inputs, so a changed plugin or CLI rebuilds it.
   - `/api/health` reports `sandbox: preparing|ready|failed`.
   - Runs wait for `ready` and never fall back to running unsandboxed.
8. **`make sandbox-check` proves the isolation from inside a real sandbox:**
   - no secrets, no Docker, no kubeconfig and no holdout;
   - no direct internet;
   - GitHub denied and the model API allowed;
   - read-only image, writable worktree, non-root.
9. **Dev escape hatch:** `AGENT_SANDBOX=off` (or `sandbox.mode: off`). The
   engine logs a warning and the UI shows `sandbox off`.

## Consequences
- The two open findings are closed by construction, not by pattern matching:
  - environment and certificate reads by agents;
  - forged gateway headers from inside the factory container.
- Hooks stay as a second layer and for behaviour rules.
- Each session costs about 1 s of container start. The first start builds the
  image (a few minutes).
- `.factory-data` is mounted into dind at `/data` as well, so sandboxes can mount
  a worktree by the same path.
- The model credential is still visible inside the sandbox; the agent needs it.
  Next step: a local auth proxy so the token never enters the sandbox.
- Anything an agent needs from the internet must be added to `sandbox.egress`
  deliberately. That is the point.
