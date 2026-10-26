import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownToLine, BellRing, CheckCircle2, ChevronDown, Play, Wrench } from "lucide-react";
import { useState } from "react";
import { api, unwrap, type AttentionItem } from "../api/client";
import { useAuth, useIsAdmin } from "../auth";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { openPanel } from "./ui/panel";

/** Who acts, as the viewer reads it. */
function useWho() {
  const auth = useAuth();
  const admin = useIsAdmin();
  const me = auth.kind === "signed-in" ? auth.me.username : null;
  return (owner: string) => {
    if (owner === "system") return { label: "the factory's operator", mine: admin };
    if (owner === "an admin") return { label: admin ? "you (admin)" : "an admin", mine: admin };
    if (auth.kind === "open" || owner === me) return { label: "you", mine: true };
    return { label: owner, mine: false };
  };
}

export function useAttention(productId?: string, includeOptional = false) {
  return useQuery({
    queryKey: ["attention", productId ?? "all", includeOptional],
    queryFn: () =>
      unwrap(
        api.GET("/api/attention", {
          params: { query: { product_id: productId, include_optional: includeOptional } },
        }),
      ),
    refetchInterval: 5_000,
  });
}

/**
 * "Needs you": everything this product is waiting for, at the top of its page.
 *
 * The engine decides what is pending (`GET /api/attention`); this panel only shows it. While a
 * change is blocked on a person, a slim bar stays at the top of the screen with the first action and
 * a "Go to" that opens the panel that does it, so nobody has to scroll to find it.
 */
export default function NeedsYou({
  productId,
  selectedChange,
  onSelectChange,
}: {
  productId: string;
  selectedChange?: string;
  onSelectChange: (changeId: string) => void;
}) {
  const q = useAttention(productId, true);
  const qc = useQueryClient();
  const who = useWho();
  const [expanded, setExpanded] = useState(false);
  const resume = useMutation({
    mutationFn: (changeId: string) =>
      unwrap(api.POST("/api/changes/{change_id}/resume", { params: { path: { change_id: changeId } } })),
    onSuccess: (_d, changeId) => {
      qc.invalidateQueries({ queryKey: ["attention"] });
      qc.invalidateQueries({ queryKey: ["change", changeId] });
      qc.invalidateQueries({ queryKey: ["product", productId] });
    },
  });
  const items = q.data ?? [];
  if (!items.length) return null;
  const blocking = items.filter((i) => i.blocking);

  const go = (i: AttentionItem) => {
    if (i.change_id !== selectedChange) onSelectChange(i.change_id);
    // the change panel mounts after a selection change: give it a moment before opening the panel
    window.setTimeout(() => openPanel(i.panel), i.change_id !== selectedChange ? 400 : 0);
  };

  // delivered: a quiet line, not a banner
  if (!blocking.length) {
    const i = items[0];
    return (
      <div
        className="flex flex-wrap items-center gap-2 rounded-xl border border-ok/40 bg-ok/5 px-4 py-2.5 text-sm"
        data-testid="needs-you"
        data-blocking="0"
      >
        <CheckCircle2 className="size-4 shrink-0 text-ok" aria-hidden />
        <span className="font-medium">Nothing waits on you.</span>
        <span className="min-w-0 flex-1 text-muted-foreground">{i.title}</span>
        <Button size="sm" variant="outline" onClick={() => go(i)}>
          <ArrowDownToLine /> Go to
        </Button>
      </div>
    );
  }

  const first = blocking[0];
  const firstWho = who(first.owner);
  return (
    <section
      className="sticky top-16 z-20 rounded-xl border border-warn/60 bg-card/95 shadow-md ring-1 ring-warn/20 backdrop-blur"
      data-testid="needs-you"
      data-blocking={blocking.length}
      aria-label="Needs you"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 sm:px-4">
        <span className="relative grid size-8 shrink-0 place-items-center rounded-full bg-warn/15 text-warn">
          <BellRing className="size-4" aria-hidden />
          <span className="absolute -right-1 -top-1 grid size-4 place-items-center rounded-full bg-warn text-[10px] font-bold text-background">
            {blocking.length}
          </span>
        </span>
        <div className="grid min-w-0 flex-1 gap-0.5">
          <span className="text-xs font-medium uppercase tracking-wide text-warn">
            {firstWho.mine ? "Needs you" : `Waiting on ${firstWho.label}`}
            {first.station ? ` · ${first.station}` : ""}
          </span>
          <span className="line-clamp-2 text-sm font-semibold sm:truncate" title={first.title}>
            {first.title}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ActionButton item={first} onGo={go} resume={resume} />
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
            title={expanded ? "Hide details" : "Show details"}
          >
            <ChevronDown className={cn("transition-transform", expanded && "rotate-180")} />
            <span className="hidden sm:inline">{blocking.length > 1 ? `All ${blocking.length}` : "Details"}</span>
          </Button>
        </div>
      </div>
      {expanded && (
        <ol className="m-0 grid max-h-[50vh] list-none gap-2 overflow-auto border-t p-3 sm:px-4">
          {blocking.map((i, n) => {
            const w = who(i.owner);
            return (
              <li
                key={`${i.change_id}-${i.kind}-${n}`}
                className="grid gap-1.5 rounded-lg border bg-background/60 p-3"
                data-testid={`needs-you-${i.kind}`}
              >
                <div className="flex flex-wrap items-baseline gap-2">
                  <strong className="text-sm">{i.title}</strong>
                  <span className={cn("pill", w.mine ? "warn" : "muted")}>{w.label}</span>
                  <span className="text-xs text-muted-foreground">
                    iteration {i.iteration}
                    {i.station ? ` · ${i.station}` : ""} · waiting {ago(i.since).replace(" ago", "")}
                  </span>
                </div>
                {i.detail && <p className="m-0 whitespace-pre-line text-sm text-muted-foreground">{i.detail}</p>}
                <div>
                  <ActionButton item={i} onGo={go} resume={resume} />
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <Problems error={resume.error} />
    </section>
  );
}

function ActionButton({
  item,
  onGo,
  resume,
}: {
  item: AttentionItem;
  onGo: (i: AttentionItem) => void;
  resume: { mutate: (id: string) => void; isPending: boolean };
}) {
  // resuming needs nothing else on the page; everything else opens the panel that does it
  if (item.kind === "resume")
    return (
      <span className="flex gap-2">
        <Button size="sm" onClick={() => resume.mutate(item.change_id)} disabled={resume.isPending}>
          <Play /> Resume
        </Button>
        <Button size="sm" variant="outline" onClick={() => onGo(item)}>
          <ArrowDownToLine /> <span className="hidden sm:inline">Evidence</span>
        </Button>
      </span>
    );
  return (
    <Button size="sm" onClick={() => onGo(item)}>
      {item.kind === "factory_hold" ? <Wrench /> : <ArrowDownToLine />} Go to
    </Button>
  );
}
