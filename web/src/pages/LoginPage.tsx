import { useQuery, useQueryClient as useClient } from "@tanstack/react-query";
import { Factory, KeyRound, LogIn } from "lucide-react";
import { useState } from "react";
import { authFetch, type Me } from "../auth";
import { Button } from "../components/ui/button";

// ADR-0052: what a failed single sign-on may say. Fixed texts for fixed codes; nothing from the identity provider.
const SSO_ERRORS: Record<string, string> = {
  state: "That sign-in link expired or came from another browser. Start again.",
  provider:
    "The sign-in provider is not available right now. Try again, or sign in with a password.",
  denied:
    "Your identity provider did not allow this sign-in, or your account is not in a group that may use the factory.",
  account: "A local account already has that name. Ask an admin.",
  disabled: "Your account is disabled. Ask an admin.",
  invalid: "The sign-in could not be verified. Try again.",
};

type Provider = { id: string; label: string };

export default function LoginPage() {
  const qc = useClient();
  const providers = useQuery({
    queryKey: ["sso-providers"],
    queryFn: async () => {
      const r = await fetch("/auth/sso", { credentials: "same-origin" });
      return r.ok ? ((await r.json()) as Provider[]) : [];
    },
    retry: false,
  });
  const ssoError = new URLSearchParams(window.location.search).get("sso_error");
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="relative grid min-h-full place-items-center overflow-hidden p-4">
      <div className="pointer-events-none absolute -top-40 left-1/2 size-[36rem] -translate-x-1/2 rounded-full bg-primary/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-48 right-0 size-[28rem] rounded-full bg-fuchsia-500/10 blur-3xl" />
      <form
        className="relative w-full max-w-sm gap-4 rounded-2xl border bg-card/80 p-7 shadow-xl backdrop-blur"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const me = await authFetch<Me>("/auth/login", {
              method: "POST",
              body: JSON.stringify(form),
            });
            qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" }); // nothing cached from before sign-in
            qc.setQueryData(["me"], { kind: "signed-in", me });
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="mb-1 grid justify-items-center gap-3 text-center">
          <div className="grid size-11 place-items-center rounded-xl bg-gradient-to-br from-primary to-fuchsia-500 text-white shadow-lg">
            <Factory className="size-5" />
          </div>
          <div>
            <h1 className="m-0 text-xl font-semibold tracking-tight">
              Agent Software Factory
            </h1>
            <p className="m-0 mt-1 text-sm text-muted-foreground">
              Sign in to continue
            </p>
          </div>
        </div>
        {ssoError && !error && (
          <p className="error">{SSO_ERRORS[ssoError] ?? SSO_ERRORS.invalid}</p>
        )}
        {(providers.data ?? []).map((p) => (
          <Button key={p.id} asChild size="lg" variant="outline">
            <a
              href={`/auth/sso/${encodeURIComponent(p.id)}/start`}
              data-testid={`sso-${p.id}`}
            >
              <KeyRound /> {p.label}
            </a>
          </Button>
        ))}
        {(providers.data ?? []).length > 0 && (
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" /> or with a password{" "}
            <span className="h-px flex-1 bg-border" />
          </div>
        )}
        <label>
          Username
          <input
            autoFocus
            autoComplete="username"
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <Button size="lg" disabled={busy || !form.username || !form.password}>
          <LogIn /> {busy ? "Signing in…" : "Sign in"}
        </Button>
        <p className="m-0 text-center text-xs text-muted-foreground">
          Locked out? An admin can reset your password, or on the host run{" "}
          <code>make reset-admin</code>.
        </p>
      </form>
    </div>
  );
}
