import { AuthError, type Auth } from "../port";

/** No sign-in (AUTH=none): every request is anonymous and nobody can sign in. */
export function noAuth(): Auth {
  return {
    enabled: false,
    signIn: async () => null,
    session: async () => null,
    signOut: async () => undefined,
    createUser: async () => {
      throw new AuthError("sign-in is off for this app (auth=none)");
    },
  };
}
