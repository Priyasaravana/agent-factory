import { useQueryClient } from "@tanstack/react-query";
import { Factory, LogIn } from "lucide-react";
import { useState } from "react";
import { authFetch, type Me } from "../auth";
import { Button } from "../components/ui/button";

export default function LoginPage() {
  const qc = useQueryClient();
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
            const me = await authFetch<Me>("/auth/login", { method: "POST", body: JSON.stringify(form) });
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
            <h1 className="m-0 text-xl font-semibold tracking-tight">Agent Factory</h1>
            <p className="m-0 mt-1 text-sm text-muted-foreground">Sign in to continue</p>
          </div>
        </div>
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
