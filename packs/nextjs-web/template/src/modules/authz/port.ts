import type { Role, User } from "@/modules/auth";

// The authorisation port: may this user (null: anonymous) do something that needs `role`?
// Checked on the server for every protected request, never only by hiding a button.
export type Decision = { ok: true } | { ok: false; status: 401 | 403; reason: string };

export interface Authz {
  check(user: User | null, role?: Role): Decision;
}

export const ALLOWED: Decision = { ok: true };
export const SIGN_IN: Decision = { ok: false, status: 401, reason: "sign in first" };
