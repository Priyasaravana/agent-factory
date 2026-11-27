# ADR-0052: Single sign-on (OpenID Connect first), the first paid feature

**Status:** proposed · 2026-11-26 (phase D, part 1) · revised 2026-11-27 after the PR #221 review (accounts bound
to the verified issuer and subject, no API tokens for SSO accounts, sign-in capacity per address, the licence
checked while running) · builds on [ADR-0012](0012-auth-gateway-container.md) (the auth gateway)
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
   gives the provider's URL; `finish` turns the callback into an `Identity`: the verified issuer and subject, a
   name, a role from groups or none, an email). It never makes users, sessions or cookies.
2. **The core keeps every security decision** (`app.py`, `/auth/sso/{id}/start` and `/callback`):
   - a random state, held server-side for 10 minutes and bound to the browser by a `SameSite=Lax`, `HttpOnly`
     cookie on `/auth/sso`; the callback must carry both, matching, once (login CSRF and replay refused);
   - the redirect address comes only from `AUTH_PUBLIC_URL`, never the request's Host; starting is refused over
     plain HTTP off this machine, like password sign-in (S-14); providers are listed only when it is set;
   - **an account is bound to the verified `(issuer, subject)` pair, never to a name** (OIDC Core §5.7): every
     account has an immutable internal `uid`; the first sign-in of an identity makes an account and binds the pair
     to its `uid` (`sso_identities`, one transaction); later sign-ins find it by the pair alone, and the name and
     email it arrives with are profile attributes. So a person renamed at the provider keeps their account, a
     different person given a reused name never inherits it (or its role), and a replaced issuer is a new
     identity. A first sign-in whose name the factory already has (a local account, or one bound to another
     identity) is refused: an identity provider can't take over `admin`, or anyone. An identity with no issuer or
     subject is refused; deleting an account deletes its bindings;
   - **a deleted account's name is retired for good** (`retired_usernames`; PR #221's second review): the engine
     knows people by name (`X-Auth-User`), so products they made, changes they asked for and who may steer are
     recorded by name. A retired name is refused for a new local account and for any identity's first sign-in,
     its former owner's included, so it can never point at someone else. Names deleted before this rule existed are
     retired at start from the audit log's `deleted user` records (never pruned), except a name an account holds
     now. Admins disable to pause someone; delete is for good. This is the interim guarantee; carrying the account's `uid` through the gateway into the engine's
     ownership checks replaces it before one sign-in serves several factories (ADR-0051 §9);
   - a disabled account stays out; the role follows the provider's groups when a mapping is set (the last-active-
     admin rule still holds), else the account keeps the role an admin gave it;
   - SSO accounts have no password (sign-in timing unchanged, `set_password` refused) and **no API tokens**:
     making one is refused, and a token made before this rule counts for nothing, so access through the identity
     provider can't outlive the person's removal there by more than a session (`AUTH_SESSION_HOURS`, the idle
     timeout; disabling the account ends its sessions at once). Local sign-in, the break-glass reset and local
     accounts' tokens work as before;
   - **sign-in capacity is shared fairly**: nginx gives `/auth/sso/` the same per-address limit as `/auth/login`,
     and the auth service keeps at most 10 pending sign-ins per address (10,000 in all), so one source can't fill
     the pool for everyone else; the provider list `/auth/sso` has its own exact location, so nginx never redirects
     it (a redirect would leave the page's origin and the CSP would block it);
   - a failed sign-in sends the browser to `/login?sso_error=<code>` with one of six fixed codes the page turns into
     fixed text: nothing the identity provider said is shown; the detail goes to the audit log and server log as
     one printable, bounded line (no forged log lines).
3. **The licensed OIDC provider** (`licensed/…/sso/`): authorization code flow with **PKCE (S256)**, state and
   **nonce**; discovery must name the configured issuer and https endpoints; every call is https, with a timeout,
   no redirects followed; the client authenticates with `client_secret_basic` (a mounted file) or as a public client.
   The ID token is checked against the provider's JWKS: **RS256, PS256 or ES256 only** (`none` and HMAC refused),
   key by `kid` (fetched once more when a provider rotates), then `iss`, `aud`, `azp`, `exp`/`iat`/`nbf` (60 s
   leeway), `nonce` and `sub`. Group mapping: `AUTH_OIDC_ADMIN_GROUPS`, `AUTH_OIDC_MEMBER_GROUPS` (when set, only
   these may sign in), `AUTH_OIDC_ALLOWED_DOMAINS` (verified email in a listed domain).
4. **Licensed and set up, or not at all.** The provider registers only when the licence covers `sso` and
   `AUTH_OIDC_ISSUER` is set, and it asks the live licence check (ADR-0051 §5, `licence.Live`) before every listing,
   start and callback: a licence that lapses while the service runs stops single sign-on without a restart, and
   local sign-in carries on. Otherwise its reason is recorded (`GET /auth/edition`, admins). The engine's Edition
   card shows the `sso` feature's licence state. The licence check is the same module the engine uses
   (`agent_factory_licensed.licence`, standard library and `cryptography` only), so the auth image runs it without
   the engine.
5. **Images and secrets.** `images/auth/Dockerfile` has `core` (default) and `enterprise` targets; enterprise adds the
   auth package's locked `enterprise` extra (`cryptography`, hash-checked) and `licensed/` with `--no-deps`. Compose
   builds `${FACTORY_EDITION:-core}` and mounts **only `.secrets/auth/`** into auth (the licence key and the client
   secret), never the factory's tokens (`tests/test_least_privilege.py`). The release builds, proves and signs both
   auth editions.

## Consequences
- An organisation signs in with its identity provider. Removing someone there ends their access when their factory
  session ends (`AUTH_SESSION_HOURS`, default 12, or 60 idle minutes), since SSO accounts hold no tokens; disabling
  the factory account ends it at once. Ending sessions on the provider's signal (back-channel logout, SCIM) is next.
- An account's name is fixed at its first sign-in; a rename at the provider shows on Admin as the identity's latest
  name, not as a new account.
- The licence key is needed in two folders (`.secrets/` for the engine's Edition card, `.secrets/auth/` for sign-in).
- **Not done here:** the account `uid` in the gateway headers and the engine's ownership records (the retired-name
  rule holds until then); SAML (part 2 of SSO); SCIM provisioning and ending sessions when the provider removes someone;
  MFA for local accounts (core, S-14's open part); the other paid features (SIEM delivery, customer KMS signing,
  compliance pack).

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §7 Security: identity | OIDC code flow with PKCE, state and nonce (OAuth 2.0 Security BCP, RFC 9700); asymmetric ID token signatures only; issuer, audience and time checks; accounts bound to the stable `(iss, sub)` pair (OIDC Core §5.7). |
| §7 Security: access ends | No long-lived tokens for SSO accounts; sessions bounded and idle-limited; a lapsed licence stops SSO while running. |
| §7 Security: authorization | A name the engine grants rights to is never reissued: deleted accounts' names are retired, for local and SSO accounts alike. |
| §7 Security: availability | Per-address limits at nginx and in the auth service keep one source from exhausting sign-in. |
| §7 Security: fail closed | Unlicensed or unconfigured means no provider; any check that fails is a refused sign-in with a fixed message; a local name is never taken over. |
| §7 Security: least privilege | The provider proves identity only; the core decides accounts and sessions; auth gets only its own secrets folder. |
| §6 Enterprise governance | Sign-ins through the provider are audited with the provider's name; roles can follow the organisation's groups. |
