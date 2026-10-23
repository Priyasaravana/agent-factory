import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Compass, MessageCircle, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";

type Draft = Record<string, { choice: string; other: string; reason: string }>;

const OTHER = "other";

/** Design decisions (ADR-0044): the stack's questions, the architect's recommendation for each, who
 * decided, and the pillar roadmap. In ask mode this is where a person answers them, and discusses
 * the design with the architect, before approving on the specification panel below. */
export default function DecisionsPanel({
  changeId,
  runStatus,
}: {
  changeId: string;
  runStatus: string;
}) {
  const qc = useQueryClient();
  const path = { params: { path: { change_id: changeId } } };
  const q = useQuery({
    queryKey: ["decisions", changeId, runStatus],
    queryFn: () => unwrap(api.GET("/api/changes/{change_id}/decisions", path)),
    refetchInterval: (query) =>
      query.state.data?.discuss_pending ? 2000 : false,
  });
  const v = q.data;
  const [draft, setDraft] = useState<Draft>({});
  const [message, setMessage] = useState("");
  // start from the sheet as it stands whenever it changes (the architect may change it)
  const sheetKey = useMemo(
    () =>
      JSON.stringify(
        v?.answers?.map((a) => [a.question, a.choice, a.other]) ?? [],
      ),
    [v],
  );
  useEffect(() => {
    if (!v) return;
    setDraft(
      Object.fromEntries(
        (v.answers ?? []).map((a) => [
          a.question,
          { choice: a.choice, other: a.other ?? "", reason: "" },
        ]),
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheetKey]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["decisions", changeId] });
    qc.invalidateQueries({ queryKey: ["change", changeId] });
  };
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/changes/{change_id}/decisions", {
          ...path,
          body: {
            answers: Object.entries(draft).map(([question, d]) => ({
              question,
              choice: d.choice,
              other: d.choice === OTHER ? d.other : null,
              reason: d.reason || null,
            })),
          },
        }),
      ),
    onSuccess: (view) => {
      qc.setQueryData(["decisions", changeId, runStatus], view);
      toast.success("Decisions saved — approve below to build");
    },
  });
  const discuss = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/decisions/discuss", {
          ...path,
          body: { message },
        }),
      ),
    onSuccess: () => {
      setMessage("");
      refresh();
    },
  });

  if (!v || (!v.asked && !(v.roadmap ?? []).length)) return null;
  const questions = v.questions ?? [];
  const answers = new Map((v.answers ?? []).map((a) => [a.question, a]));
  const changed = new Set(v.changed ?? []);
  const discussion = v.discussion ?? [];
  const editing = v.pending;
  const dirty = (v.answers ?? []).some((a) => {
    const d = draft[a.question];
    return (
      d &&
      (d.choice !== a.choice ||
        (d.choice === OTHER && d.other !== (a.other ?? "")) ||
        d.reason !== "")
    );
  });

  return (
    <section
      className={cn(
        "grid gap-3 rounded-xl border p-4",
        editing ? "border-warn/60 bg-warn/5" : "bg-card",
      )}
      data-testid="decisions-panel"
      aria-label="Design decisions"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Compass className="size-4 text-primary" />
        <h4 className="m-0">
          {editing ? "Agree the design decisions" : "Design decisions"}
        </h4>
        {v.asked && (
          <span className="pill muted">
            {questions.length} questions ·{" "}
            {v.mode === "ask" ? "a person decides" : "the architect decides"}
          </span>
        )}
        {changed.size > 0 && (
          <span
            className="pill info"
            title="answers added or changed this iteration"
          >
            {changed.size} new or changed
          </span>
        )}
      </div>

      {editing && (
        <p className="m-0 text-sm text-muted-foreground">
          The architect's recommendation is preselected for each question. Keep
          it, choose another option, or describe your own under “Other…”. Save
          your answers, then approve below.
        </p>
      )}

      {v.asked && (
        <ol className="m-0 grid list-none gap-3 p-0">
          {questions.map((question) => {
            const a = answers.get(question.id);
            const d = draft[question.id];
            const recommended = question.options.find(
              (o) => o.id === a?.recommended,
            );
            const chosen =
              a?.choice === OTHER
                ? a.other
                : question.options.find((o) => o.id === a?.choice)?.title;
            return (
              <li
                key={question.id}
                className="grid gap-2 rounded-lg border bg-card p-3"
                data-testid={`decision-${question.id}`}
              >
                <div className="flex flex-wrap items-baseline gap-2">
                  <strong className="text-sm">{question.title}</strong>
                  <span className="pill muted">{question.pillar}</span>
                  {changed.has(question.id) && (
                    <span className="pill info">changed</span>
                  )}
                  {!editing && a && (
                    <span className="ml-auto text-xs text-muted-foreground">
                      {a.decided_by}
                    </span>
                  )}
                </div>
                {!editing && (
                  <div className="text-sm">
                    <span className="font-medium">{chosen}</span>
                    {a?.rationale && (
                      <span className="text-muted-foreground">
                        {" "}
                        — {a.rationale}
                      </span>
                    )}
                  </div>
                )}
                {editing && d && (
                  <fieldset className="m-0 grid gap-1.5 border-0 p-0">
                    <legend className="sr-only">{question.title}</legend>
                    {question.options.map((o) => (
                      <label
                        key={o.id}
                        className={cn(
                          "flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm",
                          d.choice === o.id && "border-primary bg-primary/5",
                          !o.allowed && "cursor-not-allowed opacity-50",
                        )}
                        title={
                          o.allowed ? undefined : "not allowed in this lane"
                        }
                      >
                        <input
                          type="radio"
                          name={`decision-${question.id}`}
                          className="mt-1"
                          checked={d.choice === o.id}
                          disabled={!o.allowed}
                          onChange={() =>
                            setDraft({
                              ...draft,
                              [question.id]: { ...d, choice: o.id },
                            })
                          }
                        />
                        <span className="grid gap-0.5">
                          <span className="font-medium">
                            {o.title}
                            {recommended?.id === o.id && (
                              <span className="pill ok ml-2">recommended</span>
                            )}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {o.consequences}
                          </span>
                        </span>
                      </label>
                    ))}
                    <label
                      className={cn(
                        "flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm",
                        d.choice === OTHER && "border-primary bg-primary/5",
                      )}
                    >
                      <input
                        type="radio"
                        name={`decision-${question.id}`}
                        className="mt-1"
                        checked={d.choice === OTHER}
                        onChange={() =>
                          setDraft({
                            ...draft,
                            [question.id]: { ...d, choice: OTHER },
                          })
                        }
                      />
                      <span className="grid flex-1 gap-1">
                        <span className="font-medium">Other…</span>
                        {d.choice === OTHER && (
                          <input
                            aria-label={`Your choice for: ${question.title}`}
                            className="text-sm"
                            placeholder="Describe the choice you want"
                            value={d.other}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                [question.id]: { ...d, other: e.target.value },
                              })
                            }
                          />
                        )}
                      </span>
                    </label>
                    {a?.rationale && (
                      <div className="text-xs text-muted-foreground">
                        Architect: {a.rationale}
                      </div>
                    )}
                    {d.choice !== a?.recommended && (
                      <input
                        aria-label={`Reason for: ${question.title}`}
                        className="text-sm"
                        placeholder="Reason (optional, kept with the decision)"
                        value={d.reason}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            [question.id]: { ...d, reason: e.target.value },
                          })
                        }
                      />
                    )}
                  </fieldset>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {editing && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            onClick={() => save.mutate()}
            disabled={save.isPending || !dirty || v.discuss_pending}
          >
            <Check /> Save answers
          </Button>
          {!dirty && (
            <span className="text-xs text-muted-foreground">
              Nothing changed: approving keeps the architect's recommendations.
            </span>
          )}
        </div>
      )}
      {(v.problems ?? []).length > 0 && (
        <ul className="error m-0 pl-5 text-sm">
          {(v.problems ?? []).map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      {(editing || discussion.length > 0) && (
        <div
          className="grid gap-2 border-t pt-3"
          data-testid="design-discussion"
        >
          <div className="flex items-center gap-2">
            <MessageCircle className="size-4 text-primary" />
            <strong className="text-sm">Discuss with the architect</strong>
          </div>
          {discussion.map((t, i) => (
            <div
              key={i}
              className={cn(
                "max-w-[85%] rounded-lg border p-2 text-sm whitespace-pre-wrap",
                t.role === "person"
                  ? "justify-self-end bg-primary/5"
                  : "bg-card",
              )}
            >
              <div className="mb-0.5 text-xs text-muted-foreground">
                {t.role === "person" ? t.by : "Architect"}
                {(t.files ?? []).length > 0 &&
                  ` · changed ${(t.files ?? []).join(", ")}`}
              </div>
              {t.text}
            </div>
          ))}
          {v.discuss_pending && (
            <div className="text-sm text-muted-foreground" role="status">
              The architect is replying…
            </div>
          )}
          {editing && (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                discuss.mutate();
              }}
            >
              <textarea
                aria-label="Message to the architect"
                rows={2}
                className="font-sans text-sm"
                placeholder="e.g. Why local accounts and not single sign-on? Or: use the warm look"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                minLength={2}
                required
              />
              <Button
                className="w-fit"
                variant="outline"
                disabled={discuss.isPending || v.discuss_pending}
              >
                <Send /> Send
              </Button>
            </form>
          )}
        </div>
      )}
      <Problems error={save.error ?? discuss.error ?? null} />

      {(v.roadmap ?? []).length > 0 && (
        <details>
          <summary className="cursor-pointer text-sm font-medium">
            Pillar roadmap: what v0 includes, and what waits for a trigger
          </summary>
          <table className="wide mt-2">
            <thead>
              <tr>
                <td>pillar</td>
                <td>now</td>
                <td>deferred</td>
                <td>why</td>
                <td>add it when</td>
              </tr>
            </thead>
            <tbody>
              {(v.roadmap ?? []).map((r) => (
                <tr key={r.pillar}>
                  <td className="text-xs">{r.pillar}</td>
                  <td className="text-sm">{r.now}</td>
                  <td className="text-sm">{r.deferred}</td>
                  <td className="text-xs text-muted-foreground">{r.why}</td>
                  <td className="text-xs text-muted-foreground">{r.trigger}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
