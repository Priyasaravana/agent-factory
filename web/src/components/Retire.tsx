import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  History,
  PowerOff,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

type Product = components["schemas"]["Product"];
type Retirement = components["schemas"]["Retirement"];

const STEP_ICON = {
  done: <CircleCheck className="size-4 text-ok" aria-hidden />,
  skipped: <CircleMinus className="size-4 text-muted-foreground" aria-hidden />,
  failed: <CircleX className="size-4 text-bad" aria-hidden />,
  pending: <CircleDashed className="size-4 text-muted-foreground" aria-hidden />,
} as const;

/** The retire button and its dialog (roadmap J5): the reason, the product's name typed again, and
 * what will happen, in order. A retirement that stopped half-way is continued from the same dialog;
 * if the data can't be reached any more, it can go on without the final export. */
export function RetireButton({ product, disabled }: { product: Product; disabled?: boolean }) {
  const qc = useQueryClient();
  const r = product.retirement;
  const continuing = !!r && !r.finished_at;
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [withoutExport, setWithoutExport] = useState(false);
  const exportFailed = r?.steps?.some((s) => s.id === "export" && s.status === "failed");
  const go = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/retire", {
          params: { path: { product_id: product.id } },
          body: { reason: continuing ? r!.reason : reason.trim(), confirm, without_export: withoutExport },
        }),
      ),
    onSuccess: (p) => {
      for (const k of [["product", product.id], ["products"], ["operations", product.id], ["attention"], ["outcomes"]])
        qc.invalidateQueries({ queryKey: k });
      if (p.retirement?.finished_at) {
        setOpen(false);
        toast.success(`${p.title} retired`, { description: "Its decommission record is sealed." });
      } else {
        const failed = p.retirement?.steps?.find((s) => s.status === "failed");
        toast.error("Retirement stopped", { description: failed ? `${failed.title}: ${failed.detail}` : undefined });
      }
    },
  });
  const nameOk = [product.title.trim().toLowerCase(), product.slug].includes(confirm.trim().toLowerCase());
  const ready = nameOk && (continuing || reason.trim().length >= 3);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button
          variant={continuing ? "destructive" : "outline"}
          size="sm"
          disabled={disabled && !continuing}
          title={disabled ? "Wait for the change in progress to finish" : "End this product's life"}
          data-testid="retire"
        >
          <PowerOff /> {continuing ? "Continue retiring" : "Retire"}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 grid max-h-[90vh] w-[min(94vw,32rem)] -translate-x-1/2 -translate-y-1/2 gap-3 overflow-auto rounded-xl border bg-card p-5 shadow-2xl">
          <Dialog.Title className="m-0 text-base font-semibold">
            {continuing ? `Continue retiring “${product.title}”` : `Retire “${product.title}”?`}
          </Dialog.Title>
          <Dialog.Description className="m-0 text-sm text-muted-foreground">
            {continuing
              ? "The steps already done are kept; this picks up at the one that stopped."
              : "This can't be undone. In this order:"}
          </Dialog.Description>
          {continuing ? (
            <Steps retirement={r!} />
          ) : (
            <ol className="m-0 grid gap-1 pl-5 text-sm">
              <li>Nothing new can start on it.</li>
              <li>Its data is exported one last time (the export is kept; earlier backups are removed).</li>
              <li>The app is removed from the cluster and its port freed.</li>
              <li>Its repository is archived (read-only, history kept).</li>
              <li>A decommission record is sealed: its whole history and every change's evidence.</li>
            </ol>
          )}
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (ready) go.mutate();
            }}
          >
            {!continuing && (
              <label className="grid gap-1 text-sm">
                <span className="font-medium">Why is it being retired?</span>
                <textarea
                  rows={2}
                  maxLength={1000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. replaced by the new catalogue service"
                  autoFocus
                />
              </label>
            )}
            <label className="grid gap-1 text-sm">
              <span className="font-medium">
                Type <strong>{product.title}</strong> to confirm
              </span>
              <input value={confirm} onChange={(e) => setConfirm(e.target.value)} data-testid="retire-confirm" />
            </label>
            {continuing && exportFailed && (
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={withoutExport}
                  onChange={(e) => setWithoutExport(e.target.checked)}
                />
                <span>
                  Retire without the final data export
                  <span className="block text-xs text-muted-foreground">
                    Only if the data can't be reached any more. The decommission record says so.
                  </span>
                </span>
              </label>
            )}
            <Problems error={go.error} />
            <div className="flex justify-end gap-2">
              <Dialog.Close asChild>
                <Button type="button" variant="outline">
                  Cancel
                </Button>
              </Dialog.Close>
              <Button variant="destructive" disabled={!ready || go.isPending} data-testid="retire-go">
                <PowerOff /> {go.isPending ? "Retiring…" : continuing ? "Continue" : "Retire product"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Steps({ retirement }: { retirement: Retirement }) {
  return (
    <ol className="m-0 grid list-none gap-1.5 p-0 text-sm" data-testid="retire-steps">
      {(retirement.steps ?? []).map((s) => (
        <li key={s.id} className="flex items-start gap-2">
          <span className="mt-0.5">{STEP_ICON[s.status as keyof typeof STEP_ICON] ?? STEP_ICON.pending}</span>
          <span className="min-w-0">
            {s.title}
            {s.detail && (
              <span className={cn("block text-xs", s.status === "failed" ? "text-bad" : "text-muted-foreground")}>
                {s.detail}
              </span>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** A retired product's end (J5): who retired it and why, each step, and the decommission record,
 * checked again against its seal every time the page is read. */
export function Retired({ product }: { product: Product }) {
  const r = product.retirement!;
  const record = useQuery({
    queryKey: ["decommission", product.id],
    queryFn: () =>
      unwrap(api.GET("/api/products/{product_id}/decommission", { params: { path: { product_id: product.id } } })),
    enabled: !!r.finished_at,
  });
  const rec = record.data;
  return (
    <section className="grid gap-3 rounded-xl border bg-card p-4" data-testid="retired">
      <h2 className="m-0 flex items-center gap-2 text-base font-semibold">
        <Archive className="size-4 text-muted-foreground" aria-hidden />
        {r.finished_at ? `Retired ${ago(r.finished_at)}` : "Being retired"} by {r.requested_by}
      </h2>
      <p className="m-0 text-sm">{r.reason}</p>
      <Steps retirement={r} />
      {rec && (
        <p className="m-0 flex flex-wrap items-center gap-2 text-sm" data-testid="decommission-record">
          <ShieldCheck className={cn("size-4", rec.intact ? "text-ok" : "text-bad")} aria-hidden />
          {rec.intact ? "Decommission record sealed and intact" : "Decommission record does not match its seal"}
          <code className="text-xs text-muted-foreground">sha256 {rec.manifest_sha256?.slice(0, 16)}…</code>
          {!rec.intact && (
            <span className="text-xs text-bad">
              {[...(rec.changed ?? []), ...(rec.missing ?? []), ...(rec.added ?? [])].join(", ") || "manifest digest differs"}
            </span>
          )}
        </p>
      )}
    </section>
  );
}

const KIND_TONE: Record<string, string> = {
  requested: "info",
  delivered: "ok",
  decommissioned: "muted",
  incident: "bad",
  stopped: "warn",
};

/** The product's history from its first request to its decommission (J5): from what is stored and
 * sealed, never written up by an agent. */
export function ProductHistory({ productId, defaultOpen }: { productId: string; defaultOpen?: boolean }) {
  const q = useQuery({
    queryKey: ["history", productId],
    queryFn: () =>
      unwrap(api.GET("/api/products/{product_id}/history", { params: { path: { product_id: productId } } })),
  });
  const h = q.data;
  if (!h) return null;
  const entries = h.entries ?? [];
  return (
    <Panel
      id="history"
      defaultOpen={defaultOpen}
      icon={<History className="text-primary" />}
      title="History"
      summary={
        <span className="text-muted-foreground">
          {entries.length} entries{h.retired ? ", from first request to decommission" : ""}
        </span>
      }
    >
      <ol className="m-0 grid list-none gap-2 border-l p-0 pl-4" data-testid="history">
        {entries.map((e, i) => (
          <li key={`${e.at}-${i}`} className="relative grid gap-0.5 text-sm">
            <span
              className={cn(
                "absolute -left-[1.3rem] top-1.5 size-2 rounded-full bg-muted-foreground",
                e.kind === "incident" && "bg-bad",
                e.kind === "delivered" && "bg-ok",
              )}
              aria-hidden
            />
            <span className="flex flex-wrap items-center gap-2">
              <span className={cn("pill", KIND_TONE[e.kind] ?? "muted")}>{e.kind}</span>
              <span className="font-medium">{e.title}</span>
              <span className="text-xs text-muted-foreground">
                {ago(e.at)}
                {e.by ? ` · ${e.by}` : ""}
              </span>
            </span>
            {e.detail && <span className="text-muted-foreground">{e.detail}</span>}
            {e.evidence && (
              <code className="text-xs text-muted-foreground" title="sealed evidence manifest">
                sealed {e.evidence.slice(0, 12)}…
              </code>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  );
}
