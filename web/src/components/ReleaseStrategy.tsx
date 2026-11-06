import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Rocket, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

type Strategy = "in-place" | "blue-green";

const LABEL: Record<Strategy, string> = { "in-place": "In place", "blue-green": "Blue/green" };
const WHAT: Record<Strategy, string> = {
  "in-place": "The new version replaces the running one. Lighter: one copy of the app.",
  "blue-green":
    "The new version is tested beside the running one, then users are switched to it. The old one keeps running, so switching back is instant. Two copies of the app share the database.",
};

/** How a product's new versions reach its users (ADR-0046 slice 4): in place or blue/green,
 * chosen per product, defaulted by its stack pack; on blue/green, which colour serves users. */
export default function ReleaseStrategy({ productId, canChange }: { productId: string; canChange: boolean }) {
  const qc = useQueryClient();
  const path = { params: { path: { product_id: productId } } };
  const view = useQuery({
    queryKey: ["deploy-strategy", productId],
    queryFn: () => unwrap(api.GET("/api/products/{product_id}/deploy-strategy", path)),
  });
  const [pick, setPick] = useState<Strategy | null>(null);
  useEffect(() => setPick(null), [view.data?.strategy]);
  const save = useMutation({
    mutationFn: (strategy: Strategy | null) =>
      unwrap(api.POST("/api/products/{product_id}/deploy-strategy", { ...path, body: { strategy } })),
    onSuccess: (v) => {
      qc.setQueryData(["deploy-strategy", productId], v);
      toast.success(`New versions will be released ${v.strategy === "blue-green" ? "blue/green" : "in place"}`);
    },
  });
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const back = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/products/{product_id}/switch-back", { ...path, body: { reason: reason.trim() } })),
    onSuccess: (nv) => {
      qc.setQueryData(["deploy-strategy", productId], nv);
      for (const key of [["product", productId], ["ready-for"], ["next-iteration", productId]])
        qc.invalidateQueries({ queryKey: key });
      setConfirming(false);
      setReason("");
      toast.success(`Users are on ${nv.live_color} again`);
    },
  });
  const v = view.data;
  if (!v) return null;
  const current = v.strategy as Strategy;
  const chosen = pick ?? current;
  const who = v.chosen_by === "product" ? "chosen for this product" : "the stack pack's default";
  const serving =
    current === "blue-green" && v.live_color
      ? `users on ${v.live_color}${v.kept_color ? `, ${v.kept_color} kept for a switch back` : ""}`
      : null;
  return (
    <Panel
      id="releases"
      icon={<Rocket className="text-primary" />}
      title="Releases"
      summary={
        <>
          <span>
            {LABEL[current]} · {who}
          </span>
          {serving && <span className="pill muted">{serving}</span>}
        </>
      }
    >
      {current === "blue-green" && v.live_color && (
        <div className="grid gap-2 sm:grid-cols-2" data-testid="colours">
          <Colour
            role="Live"
            colour={v.live_color}
            iteration={v.live_iteration}
            url={v.live_url}
            note="what users reach"
          />
          {v.kept_color ? (
            <Colour
              role="Kept"
              colour={v.kept_color}
              iteration={v.kept_iteration}
              url={v.kept_url}
              note="still running, for an instant switch back"
            />
          ) : (
            <div className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
              Nothing kept yet: the first blue/green release has no earlier version behind it.
            </div>
          )}
        </div>
      )}
      {canChange && v.kept_color && (
        <div className="grid gap-2 rounded-lg border p-3 text-sm" data-testid="switch-back">
          {!confirming ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 text-muted-foreground">
                Something wrong with the live version? Users can go back to {v.kept_color}
                {v.kept_iteration ? ` (iteration ${v.kept_iteration})` : ""} straight away; it is still running.
              </span>
              <Button
                variant="outline"
                disabled={!v.can_switch_back}
                title={v.can_switch_back ? undefined : "Wait for the change in progress to finish"}
                onClick={() => setConfirming(true)}
              >
                <Undo2 /> Switch users back to {v.kept_color}
              </Button>
            </div>
          ) : (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (reason.trim().length >= 3) back.mutate();
              }}
            >
              <label className="grid gap-1">
                <span className="font-medium">Why switch back?</span>
                <span className="text-xs text-muted-foreground">
                  Users go to {v.kept_color} at once, then production is checked read-only. If a check fails they go
                  straight back to {v.live_color}. {v.live_color} keeps running until the next release.
                </span>
                <textarea
                  rows={2}
                  maxLength={1000}
                  autoFocus
                  value={reason}
                  placeholder="e.g. the location filter returns nothing on phones"
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <span className="flex flex-wrap gap-2">
                <Button disabled={back.isPending || reason.trim().length < 3} data-testid="switch-back-confirm">
                  <Undo2 /> {back.isPending ? "Switching…" : `Switch users to ${v.kept_color}`}
                </Button>
                <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </span>
              <Problems error={back.error} />
            </form>
          )}
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="How new versions reach users">
        {(v.options as Strategy[]).map((s) => (
          <label
            key={s}
            className={cn(
              "grid cursor-pointer gap-1 rounded-lg border p-3 text-sm",
              chosen === s ? "border-primary ring-1 ring-primary/30" : "border-border",
              !canChange && "cursor-default opacity-80",
            )}
          >
            <span className="flex items-center gap-2 font-medium">
              <input
                type="radio"
                name={`strategy-${productId}`}
                value={s}
                checked={chosen === s}
                disabled={!canChange || save.isPending}
                onChange={() => setPick(s)}
              />
              {LABEL[s]}
              {s === v.pack_default && <span className="pill muted">pack default</span>}
            </span>
            <span className="text-muted-foreground">{WHAT[s]}</span>
          </label>
        ))}
      </div>
      {canChange && ((pick !== null && pick !== current) || v.chosen_by === "product") && (
        <div className="flex flex-wrap items-center gap-2">
          {pick !== null && pick !== current && (
            <Button disabled={save.isPending} onClick={() => save.mutate(pick)}>
              Use {LABEL[pick].toLowerCase()} from the next release
            </Button>
          )}
          {v.chosen_by === "product" && pick === null && (
            <Button variant="outline" disabled={save.isPending} onClick={() => save.mutate(null)}>
              Back to the pack's default
            </Button>
          )}
        </div>
      )}
      <Problems error={save.error} />
    </Panel>
  );
}

/** One colour of a blue/green product: which iteration it runs and where it answers. */
function Colour({
  role,
  colour,
  iteration,
  url,
  note,
}: {
  role: string;
  colour: string;
  iteration?: number | null;
  url?: string | null;
  note: string;
}) {
  return (
    <div className="grid gap-1 rounded-lg border p-3 text-sm">
      <span className="flex flex-wrap items-center gap-2">
        <span className={cn("size-2.5 rounded-full", colour === "blue" ? "bg-info" : "bg-ok")} aria-hidden />
        <strong>
          {role}: {colour}
        </strong>
        {iteration ? <span className="pill muted">iteration {iteration}</span> : null}
      </span>
      <span className="text-xs text-muted-foreground">{note}</span>
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 break-all">
          {url.replace(/^https?:\/\//, "")} <ArrowUpRight className="size-3 shrink-0" />
        </a>
      )}
    </div>
  );
}
