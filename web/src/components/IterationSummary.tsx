import { useQuery } from "@tanstack/react-query";
import {
  CircleCheck,
  CircleDashed,
  CircleX,
  ListTodo,
  PackageCheck,
} from "lucide-react";
import { api, unwrap } from "../api/client";

const SEV_ORDER = ["critical", "high", "medium", "low", "info"];

/** After handover: what this iteration delivered (requirements, verified on the live app) and what
 * is left for the next one (anything not verified, the assumptions intake made, the reviewer's
 * notes and the open items on the product's work ledger). Read-only: it gathers what the spec,
 * the review and the ledger already hold. */
export default function IterationSummary({
  changeId,
  productId,
  runStatus,
  iteration,
}: {
  changeId: string;
  productId: string;
  runStatus: string;
  iteration: number;
}) {
  const spec = useQuery({
    queryKey: ["spec", changeId, runStatus],
    queryFn: () =>
      unwrap(
        api.GET("/api/changes/{change_id}/spec", {
          params: { path: { change_id: changeId } },
        }),
      ),
  });
  const ledger = useQuery({
    queryKey: ["work-items", productId],
    queryFn: () =>
      unwrap(
        api.GET("/api/products/{product_id}/work-items", {
          params: { path: { product_id: productId } },
        }),
      ),
  });
  const s = spec.data;
  if (!s) return null;
  const trace = s.traceability ?? [];
  if (!trace.length) return null;

  const live = (r: (typeof trace)[number]) => {
    const h = (r.holdout ?? []) as { passed?: boolean | null }[];
    if (h.length && h.every((x) => x.passed)) return "verified" as const;
    if (!h.length && r.no_live_check) return "no-live" as const;
    return "open" as const;
  };
  const done = trace.filter((r) => live(r) !== "open");
  const notDone = trace.filter((r) => live(r) === "open");
  const verified = trace.filter((r) => live(r) === "verified").length;
  const assumptions = s.assumptions ?? [];
  const notes = (s.review?.findings ?? []).filter(
    (f) => f.severity !== "blocker",
  );
  const open = (ledger.data ?? []).filter((i) => i.status === "open");
  const bySev = SEV_ORDER.map(
    (sev) => [sev, open.filter((i) => i.severity === sev).length] as const,
  ).filter(([, n]) => n > 0);
  const accepted = (ledger.data ?? []).filter(
    (i) => i.status === "accepted_risk",
  );
  const nextCount =
    notDone.length + assumptions.length + notes.length + (open.length ? 1 : 0);

  return (
    <section
      className="grid gap-3 rounded-xl border bg-card p-4"
      data-testid="iteration-summary"
      aria-label="Iteration summary"
    >
      <div className="grid gap-4 md:grid-cols-2">
        <div className="grid content-start gap-2">
          <h3 className="m-0 flex items-center gap-2 text-sm font-semibold">
            <PackageCheck className="size-4 text-ok" /> Delivered in iteration{" "}
            {iteration}
            <span className="pill ok ml-auto">
              {verified}/{trace.length} verified live
            </span>
          </h3>
          <ul className="m-0 grid list-none gap-1 p-0 text-sm">
            {done.map((r) => (
              <li key={r.id} className="flex items-start gap-2">
                {live(r) === "verified" ? (
                  <CircleCheck
                    className="mt-0.5 size-4 shrink-0 text-ok"
                    aria-label="verified live"
                  />
                ) : (
                  <CircleDashed
                    className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                    aria-label="tested, no live check"
                  />
                )}
                <span>
                  <code className="mr-1 text-xs">{r.id}</code>
                  {r.title}
                  <span className="text-xs text-muted-foreground">
                    {" "}
                    · {r.tests} test{r.tests === 1 ? "" : "s"}
                    {live(r) === "no-live"
                      ? ` · no live check: ${r.no_live_check}`
                      : ""}
                  </span>
                </span>
              </li>
            ))}
            {!done.length && (
              <li className="text-muted-foreground">
                Nothing verified on the live app yet.
              </li>
            )}
          </ul>
        </div>

        <div className="grid content-start gap-2">
          <h3 className="m-0 flex items-center gap-2 text-sm font-semibold">
            <ListTodo className="size-4 text-info" /> For the next iteration
            <span className="pill muted ml-auto">
              {nextCount ? `${nextCount} to look at` : "nothing open"}
            </span>
          </h3>
          {notDone.length > 0 && (
            <Group title="Not verified">
              {notDone.map((r) => (
                <li key={r.id} className="flex items-start gap-2">
                  <CircleX
                    className="mt-0.5 size-4 shrink-0 text-bad"
                    aria-hidden
                  />
                  <span>
                    <code className="mr-1 text-xs">{r.id}</code>
                    {r.title}
                  </span>
                </li>
              ))}
            </Group>
          )}
          {assumptions.length > 0 && (
            <Group
              title="Assumptions to confirm"
              hint="Intake chose these where the request was silent. Change any in the next request."
            >
              {assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </Group>
          )}
          {notes.length > 0 && (
            <Group title="Reviewer's notes">
              {notes.map((f, i) => (
                <li key={i}>
                  <span className="pill muted mr-1">{f.severity}</span>
                  {f.message}
                  {f.file && (
                    <span className="text-xs text-muted-foreground">
                      {" "}
                      · {f.file}
                    </span>
                  )}
                </li>
              ))}
            </Group>
          )}
          {(open.length > 0 || accepted.length > 0) && (
            <Group title="Work ledger">
              {open.length > 0 && (
                <li>
                  {open.length} open item{open.length === 1 ? "" : "s"} (
                  {bySev.map(([sev, n]) => `${n} ${sev}`).join(", ")}).{" "}
                  <a href="#work-ledger">Pick them for a change →</a>
                </li>
              )}
              {accepted.length > 0 && (
                <li className="text-muted-foreground">
                  {accepted.length} accepted risk
                  {accepted.length === 1 ? "" : "s"}
                  {accepted.some((i) => i.accepted_until) &&
                    `, next expiry ${new Date(
                      accepted
                        .map((i) => i.accepted_until)
                        .filter(Boolean)
                        .sort()[0] as string,
                    ).toLocaleDateString()}`}
                </li>
              )}
            </Group>
          )}
          {!nextCount && !accepted.length && (
            <p className="m-0 text-sm text-muted-foreground">
              Everything asked for is delivered and verified.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function Group({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1">
      <div className="text-xs font-medium text-muted-foreground">
        {title}
        {hint && <span className="block font-normal">{hint}</span>}
      </div>
      <ul className="m-0 grid list-none gap-1 p-0 text-sm">{children}</ul>
    </div>
  );
}
