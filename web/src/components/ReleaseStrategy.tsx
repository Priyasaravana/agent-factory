import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Rocket } from "lucide-react";
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
