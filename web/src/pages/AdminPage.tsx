import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { authFetch, useAuth, type Me } from "../auth";

type UserView = Me & { created_at: string };
type TokenView = {
  id: string;
  name: string;
  username: string;
  created_at: string;
  last_used_at: string | null;
};
type AuditRow = {
  ts: string;
  actor: string | null;
  action: string;
  target: string | null;
};

export default function AdminPage() {
  const auth = useAuth();
  const qc = useQueryClient();
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => authFetch<UserView[]>("/auth/users"),
  });
  const tokens = useQuery({
    queryKey: ["all-tokens"],
    queryFn: () => authFetch<TokenView[]>("/auth/tokens"),
  });
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: () => authFetch<AuditRow[]>("/auth/audit?limit=50"),
  });
  const [nu, setNu] = useState({ username: "", role: "member", password: "" });
  const refresh = () =>
    ["users", "all-tokens", "audit"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] }),
    );
  const m = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onSuccess: refresh,
  });
  const me = auth.kind === "signed-in" ? auth.me.username : "";

  if (auth.kind === "open")
    return (
      <p className="muted">
        Sign-in is not enabled on this factory (local mode).
      </p>
    );
  if (users.error) return <p className="error">{users.error.message}</p>;
  return (
    <div className="stack">
      <section className="card">
        <h2>Admin: users</h2>
        <p className="muted small">
          <strong>Admins</strong> change workflows, skills and users.{" "}
          <strong>Members</strong> submit products, give feedback, operate changes
          and can see everything. New users get a temporary password and must
          change it at first sign-in.
        </p>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            m.mutate(() =>
              authFetch("/auth/users", {
                method: "POST",
                body: JSON.stringify(nu),
              }),
            );
            setNu({ username: "", role: "member", password: "" });
          }}
        >
          <input
            aria-label="new username"
            placeholder="username"
            value={nu.username}
            onChange={(e) => setNu({ ...nu, username: e.target.value })}
          />
          <select
            aria-label="new role"
            value={nu.role}
            onChange={(e) => setNu({ ...nu, role: e.target.value })}
          >
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
          <input
            aria-label="temporary password"
            type="text"
            placeholder="temporary password (10+ chars)"
            value={nu.password}
            onChange={(e) => setNu({ ...nu, password: e.target.value })}
          />
          <button disabled={!nu.username || nu.password.length < 10}>
            Add user
          </button>
        </form>
        {m.error && <p className="error">{m.error.message}</p>}
        <table className="wide">
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.username} data-testid={`user-${u.username}`}>
                <td>
                  <strong>{u.username}</strong>{" "}
                  {u.username === me && (
                    <span className="small muted">(you)</span>
                  )}
                  {u.must_change && (
                    <span className="pill warn">must change password</span>
                  )}
                  {u.disabled && <span className="pill bad">disabled</span>}
                </td>
                <td>
                  <select
                    aria-label={`${u.username} role`}
                    value={u.role}
                    onChange={(e) => {
                      const role = e.target.value;
                      m.mutate(() =>
                        authFetch(`/auth/users/${u.username}`, {
                          method: "PATCH",
                          body: JSON.stringify({ role }),
                        }),
                      );
                    }}
                  >
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td className="row">
                  <button
                    className="secondary"
                    onClick={() =>
                      m.mutate(() =>
                        authFetch(`/auth/users/${u.username}`, {
                          method: "PATCH",
                          body: JSON.stringify({ disabled: !u.disabled }),
                        }),
                      )
                    }
                  >
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                  <button
                    className="secondary"
                    onClick={() => {
                      const pw = window.prompt(
                        `Temporary password for ${u.username} (10+ characters):`,
                      );
                      if (pw)
                        m.mutate(() =>
                          authFetch(`/auth/users/${u.username}`, {
                            method: "PATCH",
                            body: JSON.stringify({ reset_password: pw }),
                          }),
                        );
                    }}
                  >
                    Reset password
                  </button>
                  {u.username !== me && (
                    <button
                      className="secondary"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete ${u.username} and their API tokens?`,
                          )
                        )
                          m.mutate(() =>
                            authFetch(`/auth/users/${u.username}`, {
                              method: "DELETE",
                            }),
                          );
                      }}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h3>API tokens</h3>
        <table className="wide">
          <tbody>
            {tokens.data?.length === 0 && (
              <tr>
                <td className="muted">No tokens.</td>
              </tr>
            )}
            {tokens.data?.map((t) => (
              <tr key={t.id}>
                <td>
                  {t.name} <span className="small muted">({t.username})</span>
                </td>
                <td className="small muted">
                  {t.last_used_at
                    ? `last used ${new Date(t.last_used_at).toLocaleString()}`
                    : "never used"}
                </td>
                <td>
                  <button
                    className="secondary"
                    onClick={() =>
                      m.mutate(() =>
                        authFetch(`/auth/tokens/${t.id}`, { method: "DELETE" }),
                      )
                    }
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h3>Sign-in audit</h3>
        <table className="wide">
          <tbody>
            {audit.data?.map((a, i) => (
              <tr key={i} className="small">
                <td className="muted">{new Date(a.ts).toLocaleString()}</td>
                <td>{a.actor ?? "system"}</td>
                <td>{a.action}</td>
                <td className="muted">{a.target}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
