import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck, Undo2 } from "lucide-react";
import { useState } from "react";
import { api, unwrap, type ChangeDetail } from "../api/client";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

type Risk = NonNullable<ChangeDetail["risk"]>;

/** The change-risk check of this iteration (ADR-0027): what the diff does that
 * needs a second admin, and the approve / send-back decision while the change waits. */
export default function ChangeRiskPanel({ changeId, productId, risk }: { changeId: string; productId: string; risk: Risk }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["change", changeId] });
    qc.invalidateQueries({ queryKey: ["product", productId] });
  };
  const path = { params: { path: { change_id: changeId } }, body: { reason } };
  const approve = useMutation({
    mutationFn: () => unwrap(api.POST("/api/changes/{change_id}/risk/approve", path)),
    onSuccess: refresh,
  });
  const sendBack = useMutation({
    mutationFn: () => unwrap(api.POST("/api/changes/{change_id}/risk/send-back", path)),
    onSuccess: refresh,
  });
  const holds = (risk.findings ?? []).filter((f) => f.severity === "hold");
  const notes = (risk.findings ?? []).filter((f) => f.severity !== "hold");
  if (!holds.length && !notes.length) return null;
  const selfAt = risk.self_approval_at ? new Date(risk.self_approval_at) : null;

  return (
    <Panel
      id="risk"
      tone={risk.waiting ? "attention" : "default"}
      attention={!!risk.waiting}
      aria-label="Change risk"
      data-testid="change-risk"
      icon={risk.waiting ? <ShieldAlert className="text-warn" /> : <ShieldCheck className="text-ok" />}
      title="Change risk"
      summary={
        <span>
          {holds.length} risky · {notes.length} note{notes.length === 1 ? "" : "s"} · {risk.files_changed ?? 0} files
          changed
          {risk.approved_by ? ` · approved by ${risk.approved_by}` : ""}
        </span>
      }
    >
      <ul className="m-0 grid list-none gap-1.5 p-0 text-sm">
        {[...holds, ...notes].map((f) => (
          <li key={`${f.rule}-${f.file}-${f.line}`} className="grid gap-0.5">
            <span>
              <span className={`pill ${f.severity === "hold" ? "warn" : "muted"} mr-2`}>{f.category_title}</span>
              {f.title}
            </span>
            <code className="text-xs text-muted-foreground">
              {f.file}
              {f.line ? `:${f.line}` : ""}
              {f.snippet ? `  ${f.snippet}` : ""}
            </code>
          </li>
        ))}
      </ul>
      {risk.waiting && (
        <div className="grid gap-2">
          <p className="m-0 text-xs text-muted-foreground">
            An admin other than {risk.requested_by ?? "the requester"} approves with a reason, or anyone who can
            steer this product sends it back to be changed without them.
            {risk.break_glass && selfAt
              ? ` On a single-admin install the requester may approve as break-glass from ${selfAt.toLocaleTimeString()}.`
              : ""}
          </p>
          <textarea
            aria-label="reason"
            className="min-h-16 rounded-md border bg-background p-2 text-sm"
            placeholder="Why this is acceptable, or what to change instead (at least 10 characters)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => approve.mutate()} disabled={reason.trim().length < 10 || approve.isPending}>
              <ShieldCheck /> Approve risky changes
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => sendBack.mutate()}
              disabled={reason.trim().length < 10 || sendBack.isPending}
            >
              <Undo2 /> Send back
            </Button>
          </div>
          <Problems error={approve.error ?? sendBack.error} />
        </div>
      )}
    </Panel>
  );
}
