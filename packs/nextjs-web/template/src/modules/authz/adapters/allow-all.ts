import { ALLOWED, type Authz } from "../port";

/** Everyone may do everything (AUTHZ=allow-all): products without sign-in. */
export function allowAll(): Authz {
  return { check: () => ALLOWED };
}
