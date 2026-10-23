import { ALLOWED, SIGN_IN, type Authz } from "../port";

/** Roles from the session (AUTHZ=roles): an admin may do everything, a member what needs no role. */
export function roles(): Authz {
  return {
    check(user, role) {
      if (!user) return SIGN_IN;
      if (!role || user.role === role || user.role === "admin") return ALLOWED;
      return { ok: false, status: 403, reason: `needs the ${role} role` };
    },
  };
}
