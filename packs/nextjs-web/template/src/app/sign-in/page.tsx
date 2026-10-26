import { Page } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { auth, currentUser } from "@/modules/auth";

export const dynamic = "force-dynamic";

type Props = { searchParams: Promise<{ error?: string; next?: string }> };

export default async function SignIn({ searchParams }: Props) {
  const { error, next } = await searchParams;
  if (!auth().enabled) {
    return (
      <Page title="Sign in" className="max-w-md">
        <p>This app has no sign-in: everything is open.</p>
      </Page>
    );
  }
  const user = await currentUser();
  if (user) {
    return (
      <Page title="Signed in" className="max-w-md">
        <p>You are signed in as {user.email}.</p>
        <form method="post" action="/api/auth/sign-out">
          <Button type="submit" variant="outline">
            Sign out
          </Button>
        </form>
      </Page>
    );
  }
  return (
    <Page title="Sign in" className="max-w-md">
      <Card>
        <CardContent className="pt-6">
          <form method="post" action="/api/auth/sign-in" className="grid gap-4">
            {error && (
              <p
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                Wrong email or password. Try again.
              </p>
            )}
            <input type="hidden" name="next" value={next ?? "/"} />
            <div className="grid gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" autoComplete="username" required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </div>
            <Button type="submit" className="w-full">
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </Page>
  );
}
