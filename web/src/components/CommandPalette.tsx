import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  Boxes, FileBox, KeyRound, LayoutDashboard, LogOut, Moon, Plug, Plus, Shield, Sparkles, Sun, Workflow,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, unwrap } from "@/api/client";
import { useIsAdmin, useSignOut, useAuth } from "@/auth";
import { useTheme } from "@/theme";

export function useCommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return { open, setOpen };
}

function Item({ onSelect, icon, children, hint }: { onSelect: () => void; icon: ReactNode; children: ReactNode; hint?: string }) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-sm aria-selected:bg-muted [&_svg]:size-4 [&_svg]:text-muted-foreground"
    >
      {icon}
      <span className="flex-1">{children}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </Command.Item>
  );
}

export default function CommandPalette({ open, setOpen }: { open: boolean; setOpen: (o: boolean) => void }) {
  const nav = useNavigate();
  const isAdmin = useIsAdmin();
  const auth = useAuth();
  const signOut = useSignOut();
  const { theme, setTheme } = useTheme();
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => unwrap(api.GET("/api/orders")), enabled: open });
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: () => unwrap(api.GET("/api/workflows")), enabled: open });
  const go = (to: string) => {
    setOpen(false);
    nav(to);
  };
  const group = "px-1 py-1 text-xs [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-muted-foreground";
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[15vh] z-50 w-[min(640px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-xl border bg-popover shadow-2xl"
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Command label="Command palette" className="flex flex-col">
            <Command.Input
              autoFocus
              placeholder="Search pages, orders, workflows, actions…"
              className="h-12 rounded-none border-0 border-b bg-transparent px-4 text-sm shadow-none focus:ring-0"
            />
            <Command.List className="max-h-[60vh] overflow-auto p-1.5">
              <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">No results.</Command.Empty>
              <Command.Group heading="Go to" className={group}>
                <Item icon={<Plus />} onSelect={() => go("/?new=1")} hint="order">New order</Item>
                <Item icon={<LayoutDashboard />} onSelect={() => go("/")}>Orders</Item>
                <Item icon={<Workflow />} onSelect={() => go("/workflows")}>Workflows</Item>
                <Item icon={<Sparkles />} onSelect={() => go("/skills")}>Skills</Item>
                <Item icon={<Plug />} onSelect={() => go("/integrations")}>Integrations</Item>
                {auth.kind === "signed-in" && <Item icon={<KeyRound />} onSelect={() => go("/account")}>Account & API tokens</Item>}
                {auth.kind === "signed-in" && isAdmin && <Item icon={<Shield />} onSelect={() => go("/admin")}>Admin</Item>}
              </Command.Group>
              {(orders.data?.length ?? 0) > 0 && (
                <Command.Group heading="Orders" className={group}>
                  {orders.data!.slice(0, 8).map((o) => (
                    <Item key={o.id} icon={<FileBox />} onSelect={() => go(`/orders/${o.id}`)} hint={o.latest_status ?? ""}>
                      {o.title}
                    </Item>
                  ))}
                </Command.Group>
              )}
              {(workflows.data?.length ?? 0) > 0 && (
                <Command.Group heading="Workflows" className={group}>
                  {workflows.data!.map((w) => (
                    <Item key={w.workflow_id} icon={<Boxes />} onSelect={() => go(`/workflows/${w.workflow_id}`)} hint={`v${w.active_version}`}>
                      {w.workflow_id}
                    </Item>
                  ))}
                </Command.Group>
              )}
              <Command.Group heading="Preferences" className={group}>
                <Item
                  icon={theme === "dark" ? <Sun /> : <Moon />}
                  onSelect={() => {
                    setTheme(theme === "dark" ? "light" : "dark");
                    setOpen(false);
                  }}
                >
                  Switch to {theme === "dark" ? "light" : "dark"} theme
                </Item>
                {auth.kind === "signed-in" && (
                  <Item
                    icon={<LogOut />}
                    onSelect={() => {
                      setOpen(false);
                      void signOut();
                    }}
                  >
                    Sign out
                  </Item>
                )}
              </Command.Group>
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
