import { auth, currentUser } from "@/modules/auth";

export const dynamic = "force-dynamic";

type Props = { searchParams: Promise<{ error?: string; next?: string }> };

export default async function SignIn({ searchParams }: Props) {
  const { error, next } = await searchParams;
  if (!auth().enabled) {
    return (
      <main>
        <h1>Sign in</h1>
        <p>This app has no sign-in: everything is open.</p>
      </main>
    );
  }
  const user = await currentUser();
  if (user) {
    return (
      <main>
        <h1>Signed in</h1>
        <p>You are signed in as {user.email}.</p>
        <form method="post" action="/api/auth/sign-out">
          <button type="submit">Sign out</button>
        </form>
      </main>
    );
  }
  return (
    <main>
      <h1>Sign in</h1>
      {error && <p role="alert">Wrong email or password. Try again.</p>}
      <form method="post" action="/api/auth/sign-in">
        <input type="hidden" name="next" value={next ?? "/"} />
        <p>
          <label htmlFor="email">Email</label>
          <br />
          <input id="email" name="email" type="email" autoComplete="username" required />
        </p>
        <p>
          <label htmlFor="password">Password</label>
          <br />
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </p>
        <button type="submit">Sign in</button>
      </form>
    </main>
  );
}
