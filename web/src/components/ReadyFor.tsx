import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, CircleCheck, CircleDashed, MoveRight, UserRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { useIsAdmin } from "../auth";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

const TIERS = ["demo", "pilot", "production"] as const;
const TIER_LABEL: Record<string, string> = {
  none: "Not ready for a demo yet",
  demo: "Ready for a demo",
  pilot: "Ready for a pilot",
  production: "Ready for production",
};
const TONE: Record<string, string> = { none: "muted", demo: "info", pilot: "ok", production: "ok" };

/** What a product is ready for (principle 7, roadmap J2): the tier its latest delivery's evidence
 * proves, demo, pilot or production, worked out by the engine (never an agent's claim), what the
 * next tier still needs, and its named owner (an admin can name another). */
export default function ReadyFor({ productId, latestStatus }: { productId: string; latestStatus?: string | null }) {
  const qc = useQueryClient();
  const isAdmin = useIsAdmin();
  const path = { params: { path: { product_id: productId } } };
  const q = useQuery({
    queryKey: ["ready-for", productId, latestStatus ?? ""],
    queryFn: () => unwrap(api.GET("/api/products/{product_id}/ready-for", path)),
  });
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () => unwrap(api.POST("/api/products/{product_id}/owner", { ...path, body: { owner: name } })),
    onSuccess: (v) => {
      qc.setQueryData(["ready-for", productId, latestStatus ?? ""], v);
      setEditing(false);
      toast.success(`Owner: ${v.owner}`);
    },
  });
  const raw = q.data;
  if (!raw) return null;
  const v = { ...raw, proven: raw.proven ?? [], needs: raw.needs ?? [], unchecked_live: raw.unchecked_live ?? [] };
  const reached = TIERS.indexOf(v.tier as (typeof TIERS)[number]);

  return (
    <Panel
      id="ready-for"
      data-testid="ready-for"
      defaultOpen
      tone={v.tier === "none" ? "default" : "ok"}
      icon={<BadgeCheck className={v.tier === "none" ? "text-muted-foreground" : "text-ok"} />}
      title="Ready for"
      summary={
        <>
          <span className={cn("pill", TONE[v.tier] ?? "muted")} data-testid="ready-for-tier">
            {TIER_LABEL[v.tier] ?? v.tier}
          </span>
          {v.next_tier && (
            <span className="text-muted-foreground">
              {v.needs.length} to {v.next_tier}
            </span>
          )}
        </>
      }
    >
      <div className="grid gap-4">
        <ol className="m-0 flex list-none flex-wrap items-center gap-2 p-0 text-sm" aria-label="Tiers">
          {TIERS.map((t, i) => (
            <li key={t} className="flex items-center gap-2">
              {i > 0 && <MoveRight className="size-4 text-muted-foreground" aria-hidden />}
              <span
                className={cn(
                  "rounded-full border px-3 py-1",
                  i <= reached ? "border-ok/50 bg-ok/10 text-foreground" : "text-muted-foreground",
                  t === v.next_tier && "border-dashed",
                )}
                aria-current={i === reached ? "step" : undefined}
              >
                {t}
              </span>
            </li>
          ))}
        </ol>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="grid content-start gap-1">
            <h3 className="m-0 text-sm font-semibold">
              Proven{v.iteration ? ` by iteration ${v.iteration}` : ""}
            </h3>
            <ul className="m-0 grid list-none gap-1 p-0 text-sm" data-testid="ready-for-proven">
              {v.proven.map((p) => (
                <li key={p} className="flex items-start gap-2">
                  <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />
                  <span className="min-w-0 break-words">{p}</span>
                </li>
              ))}
              {!v.proven.length && <li className="text-muted-foreground">Nothing yet: no delivery to read.</li>}
            </ul>
            {v.unchecked_live.length > 0 && (
              <p className="m-0 text-xs text-muted-foreground">
                Tested but not checkable on the live app: {v.unchecked_live.join(", ")}.
              </p>
            )}
          </div>
          {v.next_tier && (
            <div className="grid content-start gap-1">
              <h3 className="m-0 text-sm font-semibold">For {v.next_tier}</h3>
              <ul className="m-0 grid list-none gap-1 p-0 text-sm" data-testid="ready-for-needs">
                {v.needs.map((n) => (
                  <li key={n} className="flex items-start gap-2">
                    <CircleDashed className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 break-words">{n}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {v.moved && (
          <p className="note m-0 text-sm" data-testid="ready-for-moved">
            Iteration {v.iteration} moved the product's address from <code>{v.moved.from}</code> to{" "}
            <code>{v.moved.to}</code>. Bookmarks and links to the old address no longer work.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t pt-3 text-sm">
          <UserRound className="size-4 text-muted-foreground" aria-hidden />
          <span>
            Owner: <strong data-testid="ready-for-owner">{v.owner ?? "nobody"}</strong>
            <span className="text-muted-foreground">
              {v.owner ? (v.owner_set ? " · named by an admin" : " · its creator, by default") : ""}
            </span>
          </span>
          {isAdmin && !editing && (
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={() => {
                setName(v.owner ?? "");
                setEditing(true);
              }}
            >
              Name the owner
            </Button>
          )}
        </div>
        {editing && (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) save.mutate();
            }}
          >
            <label className="grid min-w-0 flex-1 gap-1 text-sm">
              <span className="font-medium">Owner</span>
              <span className="text-xs text-muted-foreground">
                The person accountable for its incidents, upkeep and retirement.
              </span>
              <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoFocus />
            </label>
            <Button disabled={!name.trim() || save.isPending}>Save</Button>
            <Button type="button" variant="outline" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </form>
        )}
        <Problems error={save.error} />
      </div>
    </Panel>
  );
}
