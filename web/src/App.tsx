import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router-dom";
import { api, unwrap } from "./api/client";
import OrdersPage from "./pages/OrdersPage";
import OrderPage from "./pages/OrderPage";
import LinePage from "./pages/LinePage";
import LineEditPage from "./pages/LineEditPage";

export default function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => unwrap(api.GET("/api/health")),
    refetchInterval: 10_000,
  });
  const h = health.data;
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">Agent Factory</Link>
        <nav>
          <Link to="/">Orders</Link>
          <Link to="/line">The line</Link>
          <a href="/api/docs" target="_blank" rel="noreferrer">API</a>
        </nav>
        <div className="health">
          {h ? (
            <>
              <span className={`pill ${h.mode === "live" ? "ok" : "warn"}`}>{h.mode}</span>
              <span className={`pill ${h.model_auth ? "ok" : "bad"}`}>model {h.model_auth ? "ready" : "no auth"}</span>
              <span className={`pill ${h.github ? "ok" : "warn"}`}>github {h.github ? "on" : "off"}</span>
              <span className="muted">{h.active_runs} active</span>
            </>
          ) : (
            <span className="pill bad">engine unreachable</span>
          )}
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<OrdersPage />} />
          <Route path="/orders/:orderId" element={<OrderPage />} />
          <Route path="/line" element={<LinePage />} />
          <Route path="/line/edit" element={<LineEditPage />} />
        </Routes>
      </main>
    </div>
  );
}
