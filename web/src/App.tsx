import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, unwrap } from "./api/client";
import OrdersPage from "./pages/OrdersPage";
import OrderPage from "./pages/OrderPage";
import WorkflowPage from "./pages/WorkflowPage";
import WorkflowEditPage from "./pages/WorkflowEditPage";
import WorkflowsPage from "./pages/WorkflowsPage";
import SkillsPage from "./pages/SkillsPage";
import IntegrationsPage from "./pages/IntegrationsPage";
import LoginPage from "./pages/LoginPage";
import AccountPage, { ChangePassword } from "./pages/AccountPage";
import AdminPage from "./pages/AdminPage";
import { useAuth, useSignOut } from "./auth";
import ErrorBoundary from "./components/ErrorBoundary";

export default function App() {
  const auth = useAuth();
  if (auth.kind === "loading") return <p className="muted shell">Loading…</p>;
  if (auth.kind === "signed-out") return <LoginPage />;
  if (auth.kind === "signed-in" && auth.me.must_change)
    return (
      <div className="login">
        <ChangePassword forced />
      </div>
    );
  return <Shell />;
}

function UserMenu() {
  const auth = useAuth();
  const signOut = useSignOut();
  if (auth.kind !== "signed-in") return null;
  return (
    <span className="row user-menu">
      <Link to="/account" title="Account and API tokens">
        {auth.me.username}
      </Link>
      <span className="pill muted">{auth.me.role}</span>
      <button className="secondary small-btn" onClick={signOut}>
        Sign out
      </button>
    </span>
  );
}

function Shell() {
  const auth = useAuth();
  const isAdmin = auth.kind === "signed-in" && auth.me.role === "admin";
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => unwrap(api.GET("/api/health")),
    refetchInterval: 10_000,
  });
  const h = health.data;
  const location = useLocation();
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          Agent Factory
        </Link>
        <nav>
          <Link to="/">Orders</Link>
          <Link to="/workflows">Workflows</Link>
          <Link to="/skills">Skills</Link>
          <Link to="/integrations">Integrations</Link>
          {isAdmin && <Link to="/admin">Admin</Link>}
          <a href="/api/docs" target="_blank" rel="noreferrer">
            API
          </a>
        </nav>
        <div className="health">
          {h ? (
            <>
              <span className={`pill ${h.mode === "live" ? "ok" : "warn"}`}>
                {h.mode}
              </span>
              <span className={`pill ${h.model_auth ? "ok" : "bad"}`}>
                model {h.model_auth ? "ready" : "no auth"}
              </span>
              <span className={`pill ${h.github ? "ok" : "warn"}`}>
                github {h.github ? "on" : "off"}
              </span>
              <span className="muted">{h.active_runs} active</span>
            </>
          ) : (
            <span className="pill bad">engine unreachable</span>
          )}
          <UserMenu />
        </div>
      </header>
      <main>
        <ErrorBoundary resetKey={location.pathname}>
          <Routes>
            <Route path="/" element={<OrdersPage />} />
            <Route path="/orders/:orderId" element={<OrderPage />} />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/workflows/:workflowId" element={<WorkflowPage />} />
            <Route
              path="/workflows/:workflowId/edit"
              element={<WorkflowEditPage />}
            />
            <Route path="/skills" element={<SkillsPage />} />
            <Route path="/integrations" element={<IntegrationsPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route
              path="/line"
              element={<Navigate to="/workflows" replace />}
            />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}
