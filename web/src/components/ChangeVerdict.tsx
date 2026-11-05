import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleSlash, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";

type Change = components["schemas"]["Change"];

/** Statuses a person can close with a reason: stopped (held, waiting), ended (cancelled, failed), or
 * delivered but not what was wanted. A running change is cancelled first. */
const CLOSABLE = new Set([
  "held",
  "interrupted",
  "paused_limits",
  "needs_input",
  "awaiting_approval",
  "awaiting_risk_approval",
  "awaiting_feedback",
  "cancelled",
  "failed",
]);

/** A person's word on a change (J2, UX-14): a delivered iteration is accepted, or a change is closed with
 * the reason it ends here. Deployed is not accepted until someone says so. Once recorded, it is shown,
 * never changed. */
export default function ChangeVerdict({
  change,
  productId,
  canChange,
}: {
  change: Change;
  productId: string;
  canChange: boolean;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"accept" | "close" | null>(null);
  const [text, setText] = useState("");
  const path = { params: { path: { change_id: change.id } } };
  const done = () => {
    setMode(null);
    setText("");
    for (const key of [["change", change.id], ["product", productId], ["next-iteration", productId], ["attention"]])
      qc.invalidateQueries({ queryKey: key });
  };
  const accept = useMutation({
    mutationFn: () => unwrap(api.POST("/api/changes/{change_id}/accept", { ...path, body: { note: text } })),
    onSuccess: () => {
      toast.success(`Iteration ${change.iteration} accepted`);
      done();
    },
  });
  const close = useMutation({
    mutationFn: () => unwrap(api.POST("/api/changes/{change_id}/close", { ...path, body: { reason: text } })),
    onSuccess: () => {
      toast.success(`Iteration ${change.iteration} closed`);
      done();
    },
  });

  const o = change.outcome;
  if (o) {
    const accepted = o.state === "accepted";
    return (
      <div
        className={cn(
          "flex flex-wrap items-start gap-2 rounded-lg border p-3 text-sm",
          accepted ? "border-ok/40 bg-ok/5" : "bg-muted/40",
        )}
        data-testid="change-verdict-done"
      >
        {accepted ? (
          <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />
        ) : (
          <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <span className="min-w-0 break-words">
          <strong>{accepted ? "Accepted" : "Closed"}</strong> by {o.by} on {new Date(o.at).toLocaleString()}
          {o.note && <>: {o.note}</>}
          {!accepted && o.status_before === "awaiting_feedback" && (
            <span className="text-muted-foreground"> · delivered, not accepted; it stays live</span>
          )}
        </span>
      </div>
    );
  }

  const delivered = change.status === "awaiting_feedback" && change.kind !== "assess";
  if (!canChange || !CLOSABLE.has(change.status)) return null;
  const busy = accept.isPending || close.isPending;

  return (
    <div className="grid gap-2 rounded-lg border border-dashed p-3 text-sm" data-testid="change-verdict">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1">
          {delivered ? (
            <>
              <strong>Is iteration {change.iteration} what was asked for?</strong>{" "}
              <span className="text-muted-foreground">Deployed is not accepted until someone says so.</span>
            </>
          ) : (
            <>
              <strong>Iteration {change.iteration} stopped here.</strong>{" "}
              <span className="text-muted-foreground">Close it with the reason, so its record says why.</span>
            </>
          )}
        </span>
        {mode === null && (
          <span className="flex flex-wrap gap-2">
            {delivered && (
              <Button size="sm" onClick={() => setMode("accept")}>
                <ThumbsUp /> Accept
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => setMode("close")}>
              <CircleSlash /> {delivered ? "Close, not accepted" : "Close with reason"}
            </Button>
          </span>
        )}
      </div>
      {mode && (
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (mode === "accept") accept.mutate();
            else if (text.trim().length >= 3) close.mutate();
          }}
        >
          <label className="grid gap-1">
            <span className="font-medium">{mode === "accept" ? "Note (optional)" : "Why it ends here"}</span>
            <textarea
              className="font-sans text-sm"
              rows={2}
              maxLength={1000}
              autoFocus
              placeholder={mode === "accept" ? "e.g. checked on my phone, does what we need" : "e.g. rollback drill finished"}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          {mode === "close" && !delivered && change.status !== "cancelled" && change.status !== "failed" && (
            <p className="m-0 text-xs text-muted-foreground">
              Closing stops it as Cancel does: production goes back to its last healthy release if it was left serving
              this change's.
            </p>
          )}
          <span className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy || (mode === "close" && text.trim().length < 3)}>
              {mode === "accept" ? "Accept iteration " + change.iteration : "Close iteration " + change.iteration}
            </Button>
            <Button size="sm" type="button" variant="outline" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </span>
          <Problems error={accept.error ?? close.error} />
        </form>
      )}
    </div>
  );
}
