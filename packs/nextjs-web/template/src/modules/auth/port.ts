// The authentication port: who is signed in. Authorisation (what they may do) is the authz
// module's job.
export const ROLES = ["admin", "member"] as const;
export type Role = (typeof ROLES)[number];

export interface User {
  id: string;
  email: string;
  role: Role;
}

export interface Session {
  token: string; // goes into the httpOnly session cookie, never into a page or a log
  user: User;
  expiresAt: number; // ms since the epoch
}

export interface Auth {
  /** false: the no-op adapter; nobody can sign in and every request is anonymous. */
  readonly enabled: boolean;
  /** A new session for these credentials, or null when they are wrong. */
  signIn(email: string, password: string): Promise<Session | null>;
  /** The signed-in user for a session token, or null (no token, unknown or expired). */
  session(token: string | undefined): Promise<User | null>;
  signOut(token: string | undefined): Promise<void>;
  /** Add an account (an admin action). Throws on a bad email, a short password or a duplicate. */
  createUser(email: string, password: string, role: Role): Promise<User>;
}

export class AuthError extends Error {}

export const MIN_PASSWORD_LENGTH = 12;
