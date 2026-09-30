# ADR-0018: No root in the factory's containers; privileged dind is an accepted, documented risk

**Status:** accepted · 2026-09-30 · threat model: [threat-model.md](../security/threat-model.md#privileges-of-the-factorys-own-containers-adr-0018)

## Context
The factory image started as root. Its entrypoint copied dind's TLS client certs,
chowned `/data`, then dropped to uid 10001 with `setpriv`. So:
- **`docker compose exec factory …` opened a root shell.** A CLI call as root
  once left the sandbox probe folder root-owned, and CI e2e failed on it. The
  quick fix taught the CLI to drop root itself. That fix then broke the image
  build, which runs the CLI as root on purpose. Two bugs from one root default.
- **The container kept every default capability.** Its main process only lost
  them after the drop.
- **The web gateway ran nginx's master process as root.** It is the only
  published UI port.

Anyone who can run `docker compose exec` already controls Docker, so this was
not a privilege escalation for an outsider. But a compromised factory or
gateway process would have had more than it needs, and the root default kept
causing ownership bugs.

## Decision
1. **One root step, isolated: `factory-init`.**
   - A one-shot container runs `cluster/init.sh` before the factory starts. It
     copies dind's client certs into the `factory-certs` volume (owned by uid
     10001, key mode 0600) and hands `/data` to uid 10001.
   - It has no network and only CHOWN, FOWNER and DAC_OVERRIDE. A test shows it
     fails without CHOWN.
   - The factory waits for it with `service_completed_successfully`.
2. **The factory never runs as root.**
   - The image's `USER` is `10001:10001`, and compose also sets `user`.
   - It runs with `cap_drop: ALL` and `no-new-privileges`.
   - The entrypoint refuses to start as root, and a shell from
     `docker compose exec` is uid 10001.
   - Docker reads its certs from `/factory-certs`. The factory no longer mounts
     dind's cert volume.
3. **auth and web drop everything too.** auth already runs as uid 10002. web
   moves to `nginx-unprivileged` (uid 101, listening on 8080; the host port is
   still 127.0.0.1:8080). Both run with `cap_drop: ALL` and `no-new-privileges`.
4. **Proven continuously.**
   - `scripts/privilege-check.sh` (`make privilege-check`, CI e2e) reads PID 1
     of factory, auth and web from inside each container and checks:
     - the uid is not 0, and the effective and bounding capability sets are
       empty;
     - `NoNewPrivs` is set, and an `exec` shell is not root;
     - factory-init exited 0;
     - dind is the only privileged container.
   - CI `images` asserts each image's default user is non-root.
   - `test_least_privilege.py` checks the compose file, Dockerfiles and
     entrypoint before anything is built.
5. **Privileged dind is an accepted risk, not hidden.** kind inside Docker needs
   it. The mitigations and the removal path are in the threat model:
   - phase 5 delivers to real clusters, so dind is needed only locally;
   - rootless dind or Sysbox is an option for shared hosts.

The CLI's own root drop (`cli.run`, `AGENT_FACTORY_ALLOW_ROOT` for build steps)
stays as a second line of defence for images run outside compose.

## Consequences
- The attack surface of a compromised factory, auth or gateway process is its
  own uid, with no capabilities and no way to regain any.
- Existing installations need nothing. The next `make up` or `make upgrade`
  runs factory-init, which fixes the ownership of any root-owned files left by
  earlier CLI calls.
- Running the factory image by hand as root now stops with a clear message.
  Such a run needs `AGENT_FACTORY_ALLOW_ROOT=1`, and then the operator is
  responsible for file ownership.
- Adding a service means adding it to `test_least_privilege.py` and
  `privilege-check.sh`, or explaining in this ADR why it can't comply.
