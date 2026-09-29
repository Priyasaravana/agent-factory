import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

/** The auth gateway's API (its own service at /auth, not the engine). */
export type Me = {
  username: string;
  role: "admin" | "member";
  disabled: boolean;
  must_change: boolean;
};
export type AuthState =
  | { kind: "loading" }
  | { kind: "open" } // no gateway in front (local dev): single user, everything allowed
  | { kind: "signed-out" }
  | { kind: "signed-in"; me: Me };

export async function authFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body?.detail;
    const err = new Error(
      typeof detail === "string" ? detail : `request failed (${res.status})`,
    );
    (err as Error & { status?: number }).status = res.status;
    throw err;
  }
  return body as T;
}

async function loadMe(): Promise<AuthState> {
  try {
    return { kind: "signed-in", me: await authFetch<Me>("/auth/me") };
  } catch (e) {
    const status = (e as { status?: number }).status;
    if (status === 401) return { kind: "signed-out" };
    return { kind: "open" }; // 404/502: no auth gateway (dev without the auth container)
  }
}

const Ctx = createContext<AuthState>({ kind: "loading" });

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me"],
    queryFn: loadMe,
    staleTime: 60_000,
    retry: false,
  });
  useEffect(() => {
    const again = () => qc.invalidateQueries({ queryKey: ["me"] });
    window.addEventListener("factory-auth-changed", again);
    return () => window.removeEventListener("factory-auth-changed", again);
  }, [qc]);
  return (
    <Ctx.Provider value={q.data ?? { kind: "loading" }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(Ctx);
}

/** Admin in gateway mode; everyone in open (single-user) mode. */
export function useIsAdmin(): boolean {
  const a = useAuth();
  return a.kind === "open" || (a.kind === "signed-in" && a.me.role === "admin");
}

export function useSignOut() {
  const qc = useQueryClient();
  return async () => {
    await authFetch("/auth/logout", { method: "POST" }).catch(() => undefined);
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    qc.setQueryData(["me"], { kind: "signed-out" });
  };
}
