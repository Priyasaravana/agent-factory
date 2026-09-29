# ADR-0012: Authentication as a separate gateway container

**Status:** accepted · 2026-09-29

## Context
The factory had no sign-in. It must support several people with different
rights, give scripts API access, and let a company later plug in its own
single sign-on, without rewriting the factory each time.

## Decision
- **Auth is its own service** (`auth/`, `images/auth/Dockerfile`, compose
  service `auth`). It has its own SQLite database on its own volume, and only the
  environment it needs; it never receives the factory's `.env`. It owns:
  - users with roles (`admin`, `member`);
  - passwords, hashed with scrypt;
  - server-side sessions (HttpOnly, SameSite=Lax cookie);
  - API tokens (`afk_…`; only a SHA-256 is stored);
  - sign-in throttling and an audit log.
- **nginx is the gateway.** Every `/api/` request goes through `auth_request`
  to `GET /auth/check`. On success nginx forwards `X-Auth-User` /
  `X-Auth-Role`, overwriting anything the browser sent, and strips cookies and
  `Authorization` so the engine never sees credentials.
- **The engine trusts only those headers** (`AUTH_MODE=gateway`) and enforces
  roles:
  - admins change workflows and skills;
  - members order, give feedback, operate runs and read everything.

  Orders and published workflow versions record who made them. The engine's
  port is no longer published, so it can only be reached through the gateway.
- **Bootstrap and recovery:** the first admin's password comes from
  `FACTORY_ADMIN_PASSWORD`. If that is empty, a one-time password is written to
  the auth volume and must be changed at first sign-in. `make reset-admin` is
  the break-glass path.
- `AUTH_MODE=off` (default outside compose) keeps local dev and tests
  single-user.

## Consequences
- **SSO is a swap, not a rewrite.** Replace the `auth` container with an
  OIDC-aware proxy (e.g. oauth2-proxy with Okta or Entra ID) that answers the
  same check and sets the same two headers. The engine and UI don't change.
- **Known gap until the agent sandbox (phase 3):** agents run inside the
  factory container and could call the engine directly with forged headers.
  The sandbox removes that path.
- Single-instance SQLite for users. Moving to Postgres, or to an external
  identity provider, is a change inside `auth/` only.
