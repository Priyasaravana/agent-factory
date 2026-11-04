import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import Problems from "./Problems";
import { Button } from "./ui/button";

type Change = components["schemas"]["Change"];

/** A blue/green change held on its schema (ADR-0046 slice 4), or because it could only go out in place
 * over a production running two colours (L-3). Not additive: it can only go out in place. Unread: a person approves compatibility for these exact commits, saying why, or releases in
 * place. Either way the person then resumes. */
export default function SchemaHold({
  change,
  productId,
  onDone,
}: {
  change: Change;
  productId: string;
  onDone: () => void;
}) {
  const need = change.required_action;
  const [note, setNote] = useState("");
  const approve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/schema-approval", {
          params: { path: { change_id: change.id } },
          body: { note },
        }),
      ),
    onSuccess: onDone,
  });
  if (!need || (need.kind !== "approve_schema" && need.kind !== "schema_in_place" && need.kind !== "colors_in_place"))
    return null;
  const approval = change.schema_approval;
  const approved =
    approval && need.commits && approval.from_commit === need.commits[0] && approval.to_commit === need.commits[1];
  const releases = `/products/${productId}#releases`;
  return (
    <div className="grid gap-2 rounded-lg border p-3 text-sm" data-testid="schema-hold">
      {need.kind === "colors_in_place" ? (
        <div className="grid gap-1" data-testid="colors-in-place">
          {/* the engine's explanation names this layout: which colour serves, which is kept, what in place removes */}
          <p className="m-0">{change.summary || need.text}</p>
          <p className="m-0">
            <a href={releases}>Open Releases</a>
          </p>
        </div>
      ) : need.kind === "schema_in_place" ? (
        <p className="m-0">
          This change alters the schema the running version uses, so the two can't share the database. Set the product
          to <a href={releases}>In place under Releases</a>, then resume; or cancel and ask for an additive change.
        </p>
      ) : approved ? (
        <p className="m-0">
          Compatibility approved by <strong>{approval.by}</strong>: “{approval.note.replace(/[.\s]+$/, "")}”. Resume to
          release blue/green.
        </p>
      ) : (
        <>
          <p className="m-0">
            The schema check couldn't read these parts of the change between <code>{need.commits?.[0]}</code> and{" "}
            <code>{need.commits?.[1]}</code>, so it can't tell whether the version kept running for an instant switch back
            still works with the new schema:
          </p>
          <ul className="m-0 grid gap-1 pl-5">
            {(need.unsure ?? []).map((u) => (
              <li key={u}>
                <code className="whitespace-normal break-words">{u}</code>
              </li>
            ))}
          </ul>
          <label className="grid gap-1">
            <span className="font-medium">Why the running version still works (recorded with your name)</span>
            <textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. it only adds a table the old version never reads"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={note.trim().length < 10 || approve.isPending} onClick={() => approve.mutate()}>
              Approve compatibility for these commits
            </Button>
            <span className="text-muted-foreground">
              or set the product to <a href={releases}>In place</a>
            </span>
          </div>
          <Problems error={approve.error} />
        </>
      )}
    </div>
  );
}
