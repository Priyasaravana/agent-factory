import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lightbulb, Rocket, ShieldCheck, Sparkles, Wrench } from "lucide-react";
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
type Group = "features" | "quality" | "security";

const GROUPS: { key: Group; title: string; icon: ReactNode; hint: string }[] = [
  {
    key: "features",
    title: "Features",
    icon: <Lightbulb className="size-4 text-primary" aria-hidden />,
    hint: "suggested by the architect and the designer at the last delivery",
  },
  {
    key: "quality",
    title: "Quality",
    icon: <Wrench className="size-4 text-muted-foreground" aria-hidden />,
    hint: "work the quality pillars deferred",
  },
  {
    key: "security",
    title: "Security fixes",
    icon: <ShieldCheck className="size-4 text-warn" aria-hidden />,
    hint: "vulnerabilities in the image with a released fix",
  },
];

const TONE: Record<string, string> = {
  critical: "bad",
  high: "bad",
  medium: "warn",
  low: "muted",
  info: "muted",
};
const SIZE: Record<string, string> = { S: "small", M: "medium", L: "large" };

/** The kind the engine will pick (`ledger.pick_kind`), shown as a hint and used to ask what the lane
 * takes. The engine decides when the change starts: this only previews it. */
function likelyKind(items: Item[]): ChangeKind {
  const kinds = new Set(items.map((i) => i.change_kind));
  if (!items.length || kinds.has("feature")) return "feature";
  return kinds.has("bug") ? "bug" : "upkeep";
}

/**
 * "Next iteration" (roadmap J2): once a factory product's change has stopped, what its next
 * iteration can address, from `GET /next-iteration`: suggested features, deferred quality work and
 * security fixes. Tick any, add free text if you like, and start: `POST /feedback` without a kind,
 * so the engine picks it (any feature makes a feature carrying the fixes; fixes alone are upkeep).
 * Hidden while a change runs or waits on a person; a team repo keeps its pull-request box instead.
 */
export default function NextIteration({
  productId,
  latestStatus,
  onStarted,
}: {
  productId: string;
  /** the product's latest change status, from the page's own polling: when it changes (a change is
   * delivered or stops), the card asks again at once instead of waiting for its next poll */
  latestStatus?: string | null;
  onStarted?: (changeId: string) => void;
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
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [text, setText] = useState("");
  const [provided, setProvided] = useState<Record<string, Record<string, unknown>> | null>({});

  const view = q.data;
  const all: Item[] = view ? [...view.features, ...view.quality, ...view.security] : [];
  // an item may sit in two groups (a security fix is also on the work ledger once): count it once
  const chosen = [...new Map(all.filter((i) => picked.has(i.id)).map((i) => [i.id, i])).values()];
  const kind = likelyKind(chosen);
  const note = text.trim();
  const ready = chosen.length > 0 || note.length >= 3;

  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: {
            text:
              note ||
              (chosen.length === 1
                ? chosen[0].title
                : `Next iteration: ${chosen.length} items from the Next iteration card`),
            items: chosen.map((i) => i.id),
            provided: provided ?? {},
          },
        }),
      ),
    onSuccess: (change) => {
      setPicked(new Set());
      setText("");
      toast.success(`Iteration ${change.iteration} started`, {
        description: `Kind picked by the factory: ${change.kind ? KIND_LABEL[change.kind] : "change"}${
          chosen.length ? `, addressing ${chosen.length} item${chosen.length === 1 ? "" : "s"}` : ""
        }.`,
      });
      for (const key of [["next-iteration", productId], ["product", productId], ["work-items", productId], ["attention"]])
        qc.invalidateQueries({ queryKey: key });
      onStarted?.(change.id);
    },
  });

  if (!view || !view.can_start || view.latest_status === "held") return null;

  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };
  const groups = GROUPS.filter((g) => view[g.key].length > 0);
  const total = new Set(all.map((i) => i.id)).size;

  return (
    <Panel
      id="next-iteration"
      data-testid="next-iteration"
      defaultOpen
      tone="ok"
      icon={<Sparkles className="text-ok" />}
      title="Next iteration: pick what it does"
      summary={
        <>
          {groups.map((g) => (
            <span key={g.key} className="pill muted">
              {view[g.key].length} {g.title.toLowerCase()}
            </span>
          ))}
          <span>{total ? "tick what to do next, or describe a change" : "describe what to change next"}</span>
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
        {groups.map((g) => {
          const items = view[g.key];
          const allOn = items.every((i) => picked.has(i.id));
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
                      setPicked(next);
                    }}
                  >
                    {allOn ? "clear" : "tick all"}
                  </button>
                )}
              </div>
              <ul className="m-0 grid list-none gap-2 p-0">
                {items.map((i) => (
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
            </fieldset>
          );
        })}

        <label className="grid gap-1 text-sm">
          <span className="font-medium">{total ? "Anything else? (optional)" : "What should the next iteration do?"}</span>
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

        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={!ready || start.isPending || provided === null} data-testid="start-next-iteration">
            <Rocket /> Start the next iteration
          </Button>
          <span className="text-xs text-muted-foreground" data-testid="next-kind">
            {ready
              ? `${chosen.length ? `${chosen.length} ticked · ` : ""}the factory picks the kind: ${KIND_LABEL[kind]}`
              : "Tick something or describe a change."}
          </span>
        </div>
        <Problems error={start.error} />
      </form>
    </Panel>
  );
}
