import { useQuery } from "@tanstack/react-query";
import {
  Boxes, ChevronsUpDown, Command as CommandIcon, Factory, KeyRound, LayoutDashboard, LogOut, Menu, Monitor, Moon,
  Plug, Shield, Sparkles, Sun, X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { api, unwrap } from "./api/client";
import { useAuth, useSignOut } from "./auth";
import CommandPalette, { useCommandPalette } from "./components/CommandPalette";
import ErrorBoundary from "./components/ErrorBoundary";
import { Button } from "./components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "./components/ui/dropdown-menu";
import { Tip, TooltipProvider } from "./components/ui/tooltip";
import { cn } from "./lib/utils";
import AccountPage, { ChangePassword } from "./pages/AccountPage";
import AdminPage from "./pages/AdminPage";
import IntegrationsPage from "./pages/IntegrationsPage";
import LoginPage from "./pages/LoginPage";
import OrderPage from "./pages/OrderPage";
import OrdersPage from "./pages/OrdersPage";
import SkillsPage from "./pages/SkillsPage";
import WorkflowEditPage from "./pages/WorkflowEditPage";
import WorkflowPage from "./pages/WorkflowPage";
import WorkflowsPage from "./pages/WorkflowsPage";
import { useTheme, type Theme } from "./theme";

export default function App() {
  const auth = useAuth();
  if (auth.kind === "loading")
    return (
      <div className="grid h-full place-items-center text-muted-foreground">
        <Factory className="size-6 animate-pulse" />
      </div>
    );
  if (auth.kind === "signed-out") return <LoginPage />;
  if (auth.kind === "signed-in" && auth.me.must_change)
    return (
      <div className="grid min-h-full place-items-center p-4">
        <div className="w-full max-w-md">
          <ChangePassword forced />
        </div>
      </div>
    );
  return (
    <TooltipProvider>
      <Shell />
      <Toaster theme="system" richColors position="bottom-right" />
    </TooltipProvider>
  );
}

type NavItem = { to: string; label: string; icon: ReactNode; end?: boolean };

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const auth = useAuth();
  const isAdmin = auth.kind === "signed-in" && auth.me.role === "admin";
  const items: NavItem[] = [
    { to: "/", label: "Orders", icon: <LayoutDashboard />, end: true },
    { to: "/workflows", label: "Workflows", icon: <Boxes /> },
    { to: "/skills", label: "Skills", icon: <Sparkles /> },
    { to: "/integrations", label: "Integrations", icon: <Plug /> },
  ];
  if (isAdmin) items.push({ to: "/admin", label: "Admin", icon: <Shield /> });
  return (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main">
      <div className="mb-4 flex items-center gap-2.5 px-2 pt-1">
        <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-primary to-fuchsia-500 text-white shadow-md">
          <Factory className="size-4" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold">Agent Factory</div>
          <div className="text-[11px] text-muted-foreground">requirements → running software</div>
        </div>
      </div>
      {items.map((i) => (
        <NavLink
          key={i.to}
          to={i.to}
          end={i.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium text-muted-foreground no-underline transition hover:bg-muted hover:text-foreground hover:no-underline [&_svg]:size-4",
              isActive && "bg-accent text-accent-foreground",
            )
          }
        >
          {i.icon}
          {i.label}
        </NavLink>
      ))}
      <div className="mt-auto">
        <a
          href="/api/docs"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground no-underline hover:bg-muted hover:no-underline"
        >
          API reference ↗
        </a>
        <UserMenu />
      </div>
    </nav>
  );
}

function ThemeItems() {
  const { theme, setTheme } = useTheme();
  const opts: [Theme, string, ReactNode][] = [
    ["dark", "Dark", <Moon key="d" />],
    ["light", "Light", <Sun key="l" />],
    ["system", "System", <Monitor key="s" />],
  ];
  return (
    <>
      <DropdownMenuLabel>Theme</DropdownMenuLabel>
      {opts.map(([t, label, icon]) => (
        <DropdownMenuItem key={t} onSelect={() => setTheme(t)} className={cn(theme === t && "text-primary")}>
          {icon} {label}
        </DropdownMenuItem>
      ))}
    </>
  );
}

function UserMenu() {
  const auth = useAuth();
  const signOut = useSignOut();
  const name = auth.kind === "signed-in" ? auth.me.username : "local";
  const role = auth.kind === "signed-in" ? auth.me.role : "single-user";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className="mt-2 flex w-full items-center gap-2.5 rounded-lg border bg-card px-2.5 py-2 text-left text-foreground shadow-none hover:bg-muted hover:brightness-100"
          aria-label="User menu"
        >
          <span className="grid size-7 place-items-center rounded-full bg-primary/15 text-xs font-semibold uppercase text-primary">
            {name.slice(0, 2)}
          </span>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block truncate text-sm font-medium">{name}</span>
            <span className="block text-[11px] text-muted-foreground">{role}</span>
          </span>
          <ChevronsUpDown className="size-4 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        {auth.kind === "signed-in" && (
          <>
            <DropdownMenuItem asChild>
              <NavLink to="/account" className="text-foreground no-underline hover:no-underline">
                <KeyRound /> Account & API tokens
              </NavLink>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}
        <ThemeItems />
        {auth.kind === "signed-in" && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void signOut()}>
              <LogOut /> Sign out
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function StatusChips() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => unwrap(api.GET("/api/health")), refetchInterval: 10_000 });
  const h = health.data;
  if (!h)
    return (
      <span className="pill bad" title="The engine did not answer">
        engine unreachable
      </span>
    );
  return (
    <div className="hidden items-center gap-1.5 sm:flex">
      <Tip label={h.mode === "live" ? "Real agents, real deploys" : "Simulated: no model usage"} side="bottom">
        <span className={cn("pill", h.mode === "live" ? "ok" : "warn")}>
          <span className={cn("size-1.5 rounded-full bg-current", h.mode === "live" && "animate-pulse")} />
          {h.mode}
        </span>
      </Tip>
      <span className={cn("pill", h.model_auth ? "ok" : "bad")}>model {h.model_auth ? "ready" : "no auth"}</span>
      <span className={cn("pill", h.github ? "ok" : "muted")}>github {h.github ? "on" : "off"}</span>
      <span className={cn("pill", h.active_runs ? "info" : "muted")}>{h.active_runs} active</span>
    </div>
  );
}

function Shell() {
  const location = useLocation();
  const palette = useCommandPalette();
  const [mobileNav, setMobileNav] = useState(false);
  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r bg-card/40 backdrop-blur md:block">
        <Sidebar />
      </aside>
      {mobileNav && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileNav(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r bg-card">
            <Button variant="ghost" size="icon" className="absolute right-2 top-2" onClick={() => setMobileNav(false)} aria-label="Close menu">
              <X />
            </Button>
            <Sidebar onNavigate={() => setMobileNav(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/70 px-4 backdrop-blur-md md:px-6">
          <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileNav(true)} aria-label="Open menu">
            <Menu />
          </Button>
          <button
            onClick={() => palette.setOpen(true)}
            className="flex h-9 w-full max-w-sm items-center gap-2 rounded-lg border bg-card px-3 text-left text-sm font-normal text-muted-foreground shadow-none hover:bg-muted hover:brightness-100"
          >
            <CommandIcon className="size-4" />
            <span className="flex-1">Search or jump to…</span>
            <kbd className="rounded border bg-muted px-1.5 font-mono text-[11px]">⌘K</kbd>
          </button>
          <div className="ml-auto">
            <StatusChips />
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 md:px-8">
          <ErrorBoundary resetKey={location.pathname}>
            <Routes>
              <Route path="/" element={<OrdersPage />} />
              <Route path="/orders/:orderId" element={<OrderPage />} />
              <Route path="/workflows" element={<WorkflowsPage />} />
              <Route path="/workflows/:workflowId" element={<WorkflowPage />} />
              <Route path="/workflows/:workflowId/edit" element={<WorkflowEditPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/integrations" element={<IntegrationsPage />} />
              <Route path="/account" element={<AccountPage />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="/line" element={<Navigate to="/workflows" replace />} />
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
      <CommandPalette open={palette.open} setOpen={palette.setOpen} />
    </div>
  );
}
