import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ClipboardList, Lightbulb, Loader2, Rocket, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { KIND_LABEL, api, unwrap, type ChangeKind } from "../api/client";
import type { components } from "../api/schema";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import ProvidedArtifacts from "./ProvidedArtifacts";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

type Item = components["schemas"]["WorkItem"];
type Group = "features" | "quality" | "security" | "other";

const GROUPS: { key: Group; title: string; icon: ReactNode; hint: string; empty: string }[] = [
  {
    key: "features",
    title: "Features",
    icon: <Lightbulb className="size-4 text-primary" aria-hidden />,
    hint: "suggested by the architect and the designer",
    empty: "None yet. The architect and the designer suggest features each time an iteration is delivered.",
  },
  {
    key: "quality",
    title: "Quality",
    icon: <Wrench className="size-4 text-muted-foreground" aria-hidden />,
    hint: "work the quality pillars deferred",
    empty: "Nothing deferred: the quality pillars have no open work.",
  },
  {
    key: "security",
    title: "Security fixes",
    icon: <ShieldCheck className="size-4 text-warn" aria-hidden />,
    hint: "vulnerabilities in the image with a released fix",
    empty: "No open vulnerabilities with a released fix in the latest image scan.",
  },
  {
    key: "other",
    title: "Added from Work items",
    icon: <ClipboardList className="size-4 text-muted-foreground" aria-hidden />,
    hint: "picked in the Work items panel",
    empty: "",
  },
];

/** Long groups show this many until "show more" (ticked items always show). */
const FIRST = 3;

const TONE: Record<string, string> = {
  critical: "bad",
  high: "bad",
  medium: "warn",
  low: "muted",
  info: "muted",
};
const SIZE: Record<string, string> = { S: "small", M: "medium", L: "large" };
const KIND_MEANS: Partial<Record<ChangeKind, string>> = {
  feature: "new behaviour; any fixes you ticked go with it",
  upkeep: "fixes only; the app behaves as before",
  bug: "a bug fix",
};

/** The kind the engine will pick (`ledger.pick_kind`), previewed in the summary and used to ask what
 * the lane takes. The engine decides when the change starts. */
function likelyKind(items: Item[]): ChangeKind {
  const kinds = new Set(items.map((i) => i.change_kind));
  if (!items.length || kinds.has("feature")) return "feature";
  return kinds.has("bug") ? "bug" : "upkeep";
}

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * "Next iteration" (roadmap J2): the one place a factory product's next iteration is chosen and
 * started. From `GET /next-iteration`: suggested features, deferred quality work and security fixes,
 * plus anything added from the Work items panel (`picked` is owned by the page so both share it).
 * Every group shows, an empty one says why; a summary says what will start before you start it;
 * `POST /feedback` goes without a kind, so the engine picks it. While a change runs the card says
 * what is running; while one waits on a person, "Needs you" leads instead. Team repos keep their
 * pull-request box.
 */
export default function NextIteration({
  productId,
  latestStatus,
  picked,
  onPickedChange,
  onStarted,
  onFollow,
}: {
  productId: string;
  /** the product's latest change status, from the page's own polling: when it changes (a change is
   * delivered or stops), the card asks again at once instead of waiting for its next poll */
  latestStatus?: string | null;
  picked: Set<string>;
  onPickedChange: (next: Set<string>) => void;
  onStarted?: (changeId: string) => void;
  /** show this change below (the running one) */
  onFollow?: (changeId: string) => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["next-iteration", productId, latestStatus ?? ""],
    queryFn: () =>
      unwrap(
        api.GET("/api/products/{product_id}/next-iteration", {
          params: { path: { product_id: productId } },
        }),
      ),
    refetchInterval: 8_000,
  });
  // the same query the Work items panel uses (shared cache): items added from there that sit in no group
  const ledger = useQuery({
    queryKey: ["work-items", productId],
    queryFn: () =>
      unwrap(api.GET("/api/products/{product_id}/work-items", { params: { path: { product_id: productId } } })),
    refetchInterval: 8_000,
  });
  const [text, setText] = useState("");
  const [more, setMore] = useState<Set<Group>>(new Set());
  const [provided, setProvided] = useState<Record<string, Record<string, unknown>> | null>({});

  const view = q.data;
  const grouped: Item[] = view ? [...view.features, ...view.quality, ...view.security] : [];
  const inGroups = new Set(grouped.map((i) => i.id));
  const other = (ledger.data ?? []).filter((i) => picked.has(i.id) && !inGroups.has(i.id) && i.status === "open");
  const lists: Record<Group, Item[]> = {
    features: view?.features ?? [],
    quality: view?.quality ?? [],
    security: view?.security ?? [],
    other,
  };
  const all = [...grouped, ...other];
  // an item may sit in two groups: count it once
  const chosen = [...new Map(all.filter((i) => picked.has(i.id)).map((i) => [i.id, i])).values()];
  const kind = likelyKind(chosen);
  const note = text.trim();
  const ready = chosen.length > 0 || note.length >= 3;
  const nextNumber = (view?.latest_iteration ?? 0) + 1;

  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: {
            // the first line is what the card shows while it runs: the titles say more than a count
            text: note || chosen.map((i) => i.title).join("; "),
            items: chosen.map((i) => i.id),
            provided: provided ?? {},
          },
        }),
      ),
    onSuccess: (change) => {
      onPickedChange(new Set());
      setText("");
      toast.success(`Iteration ${change.iteration} started`, {
        description: `${change.kind ? KIND_LABEL[change.kind] : "Change"}${
          chosen.length ? `, addressing ${plural(chosen.length, "item")}` : ""
        }.`,
      });
      for (const key of [["next-iteration", productId], ["product", productId], ["work-items", productId], ["attention"]])
        qc.invalidateQueries({ queryKey: key });
      onStarted?.(change.id);
    },
  });

  if (!view || view.latest_status === "held") return null;

  if (!view.can_start) {
    // a change is running: say what, instead of disappearing
    return (
      <Panel
        id="next-iteration"
        data-testid="next-iteration"
        defaultOpen
        icon={<Loader2 className="animate-spin text-primary" />}
        title={`Iteration ${view.latest_iteration ?? ""} is running`}
        summary={
          <>
            {view.latest_kind && <span className="pill info">{KIND_LABEL[view.latest_kind]}</span>}
            <span className="line-clamp-1">{view.latest_request ?? ""}</span>
          </>
        }
      >
        <div className="grid gap-3 text-sm" data-testid="next-running">
          {view.latest_request && <p className="m-0 break-words">“{view.latest_request}”</p>}
          <p className="m-0 text-muted-foreground">
            You can pick iteration {nextNumber} here once this one is delivered.
            {picked.size > 0 && ` ${plural(picked.size, "item")} from Work items will be waiting for it.`}
          </p>
          {view.latest_change_id && onFollow && (
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() => onFollow(view.latest_change_id!)}
            >
              Follow it <ArrowRight />
            </Button>
          )}
        </div>
      </Panel>
    );
  }

  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onPickedChange(next);
  };
  const counts = (g: Group) => lists[g].filter((i) => picked.has(i.id)).length;
  const parts = [
    counts("features") && plural(counts("features"), "feature"),
    counts("quality") && plural(counts("quality"), "quality item"),
    counts("security") && plural(counts("security"), "security fix", "security fixes"),
    counts("other") && plural(counts("other"), "work item"),
    note.length >= 3 && "your note",
  ].filter(Boolean) as string[];

  return (
    <Panel
      id="next-iteration"
      data-testid="next-iteration"
      defaultOpen
      tone="ok"
      icon={<Sparkles className="text-ok" />}
      title={`Next iteration (${nextNumber}): pick what it does`}
      summary={
        <>
          <span className="pill muted">{plural(lists.features.length, "feature")}</span>
          <span className="pill muted">{plural(lists.quality.length, "quality item")}</span>
          <span className="pill muted">{plural(lists.security.length, "security fix", "security fixes")}</span>
          <span>tick what to do next, or describe a change</span>
        </>
      }
    >
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) start.mutate();
        }}
      >
        {GROUPS.filter((g) => g.key !== "other" || other.length > 0).map((g) => {
          const items = lists[g.key];
          const allOn = items.length > 0 && items.every((i) => picked.has(i.id));
          const open = more.has(g.key);
          const visible = open ? items : items.filter((i, n) => n < FIRST || picked.has(i.id));
          const hidden = items.length - visible.length;
          return (
            <fieldset
              key={g.key}
              aria-label={g.title}
              className="m-0 grid gap-2 border-0 p-0"
              data-testid={`next-${g.key}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                {g.icon}
                <span className="text-sm font-semibold">{g.title}</span>
                <span className="text-xs text-muted-foreground">{g.hint}</span>
                {items.length > 1 && (
                  <button
                    type="button"
                    className="tab ml-auto"
                    onClick={() => {
                      const next = new Set(picked);
                      for (const i of items) {
                        if (allOn) next.delete(i.id);
                        else next.add(i.id);
                      }
                      onPickedChange(next);
                    }}
                  >
                    {allOn ? "clear" : "tick all"}
                  </button>
                )}
              </div>
              {items.length === 0 ? (
                <p className="m-0 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground" data-testid={`next-${g.key}-empty`}>
                  {g.empty}
                </p>
              ) : (
                <ul className="m-0 grid list-none gap-2 p-0">
                  {visible.map((i) => (
                    <li key={i.id}>
                      <label
                        className={cn(
                          "flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors",
                          picked.has(i.id) ? "border-primary/60 bg-primary/5" : "hover:bg-muted/40",
                        )}
                        data-testid={`next-item-${i.id}`}
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 size-4 shrink-0"
                          checked={picked.has(i.id)}
                          onChange={() => toggle(i.id)}
                        />
                        <span className="grid min-w-0 flex-1 gap-1">
                          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                            <span className="font-medium">{i.title}</span>
                            <span className={cn("pill", TONE[i.severity] ?? "muted")}>
                              {g.key === "features" ? `${i.severity} value` : i.severity}
                            </span>
                            {g.key === "features" && i.effort && (
                              <span className="pill muted">{SIZE[i.effort] ?? i.effort}</span>
                            )}
                          </span>
                          {i.detail && (
                            <span className="line-clamp-3 whitespace-pre-line break-words text-xs text-muted-foreground">
                              {i.detail}
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              {(hidden > 0 || (open && items.length > FIRST)) && (
                <button
                  type="button"
                  className="tab w-fit"
                  onClick={() => {
                    const next = new Set(more);
                    if (open) next.delete(g.key);
                    else next.add(g.key);
                    setMore(next);
                  }}
                >
                  {open ? "show fewer" : `show ${hidden} more`}
                </button>
              )}
            </fieldset>
          );
        })}

        <label className="grid gap-1 text-sm">
          <span className="font-medium">{all.length ? "Anything else? (optional)" : "What should the next iteration do?"}</span>
          <textarea
            className="font-sans text-sm"
            rows={3}
            placeholder="e.g. show the signed-in email in the phone header…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={20_000}
          />
        </label>

        <ProvidedArtifacts productId={productId} kind={kind} onChange={setProvided} />

        <div
          className={cn(
            "grid gap-3 rounded-lg border p-3 text-sm",
            ready ? "border-primary/40 bg-primary/5" : "bg-muted/40",
          )}
          data-testid="next-summary"
        >
          <p className="m-0" data-testid="next-kind">
            {ready ? (
              <>
                Iteration {nextNumber} will be a <strong>{KIND_LABEL[kind]}</strong> ({KIND_MEANS[kind] ?? KIND_LABEL[kind]}
                ): {parts.join(", ")}.
              </>
            ) : (
              <span className="text-muted-foreground">Tick something above or describe a change.</span>
            )}
          </p>
          <Button className="w-fit" disabled={!ready || start.isPending || provided === null} data-testid="start-next-iteration">
            <Rocket /> Start iteration {nextNumber}
          </Button>
        </div>
        <Problems error={start.error} />
      </form>
    </Panel>
  );
}
