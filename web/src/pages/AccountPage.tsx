import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { authFetch, useAuth } from "../auth";

type TokenView = {
  id: string;
  name: string;
  username: string;
  created_at: string;
  last_used_at: string | null;
};

export function ChangePassword({ forced = false }: { forced?: boolean }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    current_password: "",
    new_password: "",
    confirm: "",
  });
  const change = useMutation({
    mutationFn: () =>
      authFetch("/auth/me/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: f.current_password,
          new_password: f.new_password,
        }),
      }),
    onSuccess: () => {
      setF({ current_password: "", new_password: "", confirm: "" });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
  const mismatch = f.confirm.length > 0 && f.confirm !== f.new_password;
  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        change.mutate();
      }}
    >
      <h3>{forced ? "Choose a new password" : "Change password"}</h3>
      {forced && (
        <p className="note">
          Your password was set by an admin or generated. Choose your own to
          continue.
        </p>
      )}
      <label>
        Current password
        <input
          type="password"
          autoComplete="current-password"
          value={f.current_password}
          onChange={(e) => setF({ ...f, current_password: e.target.value })}
        />
      </label>
      <label>
        New password (at least 10 characters)
        <input
          type="password"
          autoComplete="new-password"
          value={f.new_password}
          onChange={(e) => setF({ ...f, new_password: e.target.value })}
        />
      </label>
      <label>
        Repeat new password
        <input
          type="password"
          autoComplete="new-password"
          value={f.confirm}
          onChange={(e) => setF({ ...f, confirm: e.target.value })}
        />
      </label>
      {mismatch && <p className="error small">The passwords do not match.</p>}
      {change.error && <p className="error">{change.error.message}</p>}
      {change.isSuccess && !forced && (
        <p className="note">
          Password changed. Other sessions were signed out.
        </p>
      )}
      <button
        disabled={
          change.isPending ||
          mismatch ||
          f.new_password.length < 10 ||
          !f.current_password
        }
      >
        Change password
      </button>
    </form>
  );
}

export default function AccountPage() {
  const auth = useAuth();
  const qc = useQueryClient();
  const tokens = useQuery({
    queryKey: ["my-tokens"],
    queryFn: () => authFetch<TokenView[]>("/auth/me/tokens"),
  });
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () =>
      authFetch<TokenView & { token: string }>("/auth/me/tokens", {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    onSuccess: (t) => {
      setCreated(t.token);
      setName("");
      qc.invalidateQueries({ queryKey: ["my-tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      authFetch(`/auth/me/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["my-tokens"] }),
  });
  if (auth.kind !== "signed-in")
    return (
      <p className="muted">
        Sign-in is not enabled on this factory (local mode).
      </p>
    );
  return (
    <div className="stack">
      <section className="card">
        <h2>Account</h2>
        <p>
          Signed in as <strong>{auth.me.username}</strong>{" "}
          <span className="pill info">{auth.me.role}</span>
        </p>
      </section>
      <ChangePassword />
      <section className="card">
        <h3>API tokens</h3>
        <p className="muted small">
          For scripts and CI: send{" "}
          <code>Authorization: Bearer &lt;token&gt;</code> to{" "}
          <code>/api/…</code>. A token acts as you, with your role. It is shown
          once; only its hash is stored.
        </p>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <input
            placeholder="token name, e.g. ci"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button disabled={!name || create.isPending}>Create token</button>
        </form>
        {created && (
          <p className="note">
            New token (copy it now; it will not be shown again):{" "}
            <code data-testid="new-token">{created}</code>
          </p>
        )}
        <table className="wide">
          <tbody>
            {tokens.data?.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td className="small muted">
                  created {new Date(t.created_at).toLocaleString()}
                </td>
                <td className="small muted">
                  {t.last_used_at
                    ? `last used ${new Date(t.last_used_at).toLocaleString()}`
                    : "never used"}
                </td>
                <td>
                  <button
                    className="secondary"
                    onClick={() => revoke.mutate(t.id)}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
