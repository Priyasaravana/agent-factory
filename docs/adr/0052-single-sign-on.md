# ADR-0052: Single sign-on (OpenID Connect first), the first paid feature

**Status:** proposed · 2026-11-26 (phase D, part 1) · builds on [ADR-0012](0012-auth-gateway-container.md) (the auth gateway)
and [ADR-0051](0051-open-core-seam.md) (the open core seam)

## Context
Phase D's first paid feature is single sign-on: organisations running several teams want people to sign in with
their identity provider (Entra ID, Okta, Google Workspace, Keycloak, Auth0), not a password the factory keeps.
OIDC comes first; SAML follows. The auth service is its own package and container, never imports the engine,
and must stay usable when an identity provider is down or a licence ends. Safety stays in core (ADR-0051 §4):
local sign-in can't be switched off by an extension.

## Decision
1. **The auth service gets its own seam** (`auth/src/factory_auth/extensions.py`), mirroring the engine's: entry
   point group `factory_auth.extensions`, one point, `sign_in`. A provider only **proves who someone is** (`start`
   gives the provider's URL; `finish` turns the callback into an `Identity`: name, role from groups or none,
   email). It never makes users, sessions or cookies.
2. **The core keeps every security decision** (`app.py`, `/auth/sso/{id}/start` and `/callback`):
   - a random state, held server-side for 10 minutes and bound to the browser by a `SameSite=Lax`, `HttpOnly`
     cookie on `/auth/sso`; the callback must carry both, matching, once (login CSRF and replay refused);
   - the redirect address comes only from `AUTH_PUBLIC_URL`, never the request's Host; starting is refused over
     plain HTTP off this machine, like password sign-in (S-14); providers are listed only when it is set;
   - **an identity provider can never take over an account it didn't make**: accounts carry a `source` (`local` or
     `sso:<id>`); a name that exists from elsewhere is refused, so `admin` stays local;
   - a disabled account stays out; the role follows the provider's groups when a mapping is set (the last-active-
     admin rule still holds), else the account keeps the role an admin gave it;
   - SSO accounts have no password (sign-in timing unchanged, `set_password` refused), and local sign-in, the
     break-glass reset and API tokens work as before;
   - a failed sign-in sends the browser to `/login?sso_error=<code>` with one of six fixed codes the page turns into
     fixed text: nothing the identity provider said is shown; the detail goes to the audit log and server log.
3. **The licensed OIDC provider** (`licensed/…/sso/`): authorization code flow with **PKCE (S256)**, state and
   **nonce**; discovery must name the configured issuer and https endpoints; every call is https, with a timeout,
   no redirects followed; the client authenticates with `client_secret_basic` (a mounted file) or as a public client.
   The ID token is checked against the provider's JWKS: **RS256, PS256 or ES256 only** (`none` and HMAC refused),
   key by `kid` (fetched once more when a provider rotates), then `iss`, `aud`, `azp`, `exp`/`iat`/`nbf` (60 s
   leeway), `nonce` and `sub`. Group mapping: `AUTH_OIDC_ADMIN_GROUPS`, `AUTH_OIDC_MEMBER_GROUPS` (when set, only
   these may sign in), `AUTH_OIDC_ALLOWED_DOMAINS` (verified email in a listed domain).
4. **Licensed and set up, or not at all.** The provider registers only when the licence covers `sso` and
   `AUTH_OIDC_ISSUER` is set; otherwise its reason is recorded (`GET /auth/edition`, admins). The engine's Edition
   card shows the `sso` feature's licence state. The licence check is the same module the engine uses
   (`agent_factory_licensed.licence`, standard library and `cryptography` only), so the auth image runs it without
   the engine.
5. **Images and secrets.** `images/auth/Dockerfile` has `core` (default) and `enterprise` targets; enterprise adds the
   auth package's locked `enterprise` extra (`cryptography`, hash-checked) and `licensed/` with `--no-deps`. Compose
   builds `${FACTORY_EDITION:-core}` and mounts **only `.secrets/auth/`** into auth (the licence key and the client
   secret), never the factory's tokens (`tests/test_least_privilege.py`). The release builds, proves and signs both
   auth editions.

## Consequences
- An organisation signs in with its identity provider; offboarding there ends access at the next sign-in (sessions
  still end by `AUTH_SESSION_HOURS` and the idle timeout).
- The licence key is needed in two folders (`.secrets/` for the engine's Edition card, `.secrets/auth/` for sign-in).
- **Not done here:** SAML (part 2 of SSO); SCIM provisioning and ending sessions when the provider removes someone;
  MFA for local accounts (core, S-14's open part); the other paid features (SIEM delivery, customer KMS signing,
  compliance pack).

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §7 Security: identity | OIDC code flow with PKCE, state and nonce (OAuth 2.0 Security BCP, RFC 9700); asymmetric ID token signatures only; issuer, audience and time checks. |
| §7 Security: fail closed | Unlicensed or unconfigured means no provider; any check that fails is a refused sign-in with a fixed message; a local name is never taken over. |
| §7 Security: least privilege | The provider proves identity only; the core decides accounts and sessions; auth gets only its own secrets folder. |
| §6 Enterprise governance | Sign-ins through the provider are audited with the provider's name; roles can follow the organisation's groups. |
