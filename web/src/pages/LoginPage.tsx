import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { authFetch, type Me } from "../auth";

export default function LoginPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <form
        className="card"
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
        <h2>Agent Factory</h2>
        <p className="muted small">Sign in to continue.</p>
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
        <button disabled={busy || !form.username || !form.password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small">
          Locked out? An admin can reset your password, or on the host run{" "}
          <code>make reset-admin</code>.
        </p>
      </form>
    </div>
  );
}
