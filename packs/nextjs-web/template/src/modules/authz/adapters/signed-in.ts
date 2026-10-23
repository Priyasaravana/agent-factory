import { ALLOWED, SIGN_IN, type Authz } from "../port";

/** Any signed-in user may do everything (AUTHZ=signed-in); roles are not checked. */
export function signedIn(): Authz {
  return { check: (user) => (user ? ALLOWED : SIGN_IN) };
}
