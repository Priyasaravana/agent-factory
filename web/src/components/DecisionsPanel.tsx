import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Compass, HelpCircle, MessageCircle, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";

type Draft = Record<string, { choice: string; other: string; reason: string }>;

const OTHER = "other";

/** Align with the architect (ADR-0044 §1): the stack's design questions with the architect's
 * recommendation preselected, the specification's open questions with its proposed answers, and a
 * thread. Choices, answers and comments are collected as a draft and sent together, as one input to
 * the architect, which replies and updates the spec, design and plan. Approve builds what was agreed. */
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
  const [openDraft, setOpenDraft] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  // start from the sheet as it stands whenever it changes (the architect may change it)
  const sheetKey = useMemo(
    () =>
      JSON.stringify([
        v?.answers?.map((a) => [a.question, a.choice, a.other]) ?? [],
        v?.open_questions?.map((o) => [o.id, o.answer]) ?? [],
      ]),
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
    setOpenDraft(
      Object.fromEntries((v.open_questions ?? []).map((o) => [o.id, o.answer])),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheetKey]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["decisions", changeId] });
    qc.invalidateQueries({ queryKey: ["change", changeId] });
    qc.invalidateQueries({ queryKey: ["spec", changeId] });
    qc.invalidateQueries({ queryKey: ["product"] });
  };
  const changedChoices = (v?.answers ?? []).filter((a) => {
    const d = draft[a.question];
    return (
      d &&
      (d.choice !== a.choice ||
        (d.choice === OTHER && d.other.trim() !== (a.other ?? "")) ||
        d.reason.trim() !== "")
    );
  });
  const changedOpen = (v?.open_questions ?? []).filter(
    (o) =>
      (openDraft[o.id] ?? "").trim() &&
      (openDraft[o.id] ?? "").trim() !== o.answer,
  );
  const pendingCount =
    changedChoices.length + changedOpen.length + (message.trim() ? 1 : 0);
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/align", {
          ...path,
          body: {
            answers: changedChoices.map((a) => {
              const d = draft[a.question];
              return {
                question: a.question,
                choice: d.choice,
                other: d.choice === OTHER ? d.other : null,
                reason: d.reason || null,
              };
            }),
            open_answers: changedOpen.map((o) => ({
              id: o.id,
              answer: openDraft[o.id].trim(),
            })),
            message: message.trim(),
          },
        }),
      ),
    onSuccess: () => {
      setMessage("");
      refresh();
    },
  });
  const approve = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/changes/{change_id}/spec/approve", path)),
    onSuccess: () => {
      refresh();
      toast.success("Approved — building what you agreed");
    },
  });

  if (!v) return null;
  const questions = v.questions ?? [];
  const openQs = v.open_questions ?? [];
  const roadmap = v.roadmap ?? [];
  const discussion = v.discussion ?? [];
  const reviewing = v.pending;
  if (!reviewing && !questions.length && !openQs.length && !roadmap.length)
    return null;
  const answers = new Map((v.answers ?? []).map((a) => [a.question, a]));
  const changed = new Set(v.changed ?? []);
  const busy = v.discuss_pending || send.isPending;

  return (
    <section
      className={cn(
        "grid gap-3 rounded-xl border p-4",
        reviewing ? "border-warn/60 bg-warn/5" : "bg-card",
      )}
      data-testid="decisions-panel"
      aria-label="Design decisions"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Compass className="size-4 text-primary" />
        <h4 className="m-0">
          {reviewing ? "Align with the architect" : "Design decisions"}
        </h4>
        {questions.length > 0 && (
          <span className="pill muted">
            {questions.length} design choices ·{" "}
            {v.mode === "ask" ? "you decide" : "the architect decides"}
          </span>
        )}
        {openQs.length > 0 && (
          <span className="pill muted">{openQs.length} open questions</span>
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

      {reviewing && (
        <p className="m-0 text-sm text-muted-foreground">
          The architect has turned your request into a specification and a plan,
          and recommends an answer to each choice below. Change any of them, add
          comments or questions, and send everything together: the architect
          replies and updates the spec, design and plan. Approve when you are
          aligned.
        </p>
      )}

      {openQs.length > 0 && (
        <div className="grid gap-2" data-testid="open-questions">
          <strong className="flex items-center gap-2 text-sm">
            <HelpCircle className="size-4 text-primary" /> Open questions from
            your request
          </strong>
          <ol className="m-0 grid list-none gap-2 p-0">
            {openQs.map((o) => (
              <li
                key={o.id}
                className="grid gap-1.5 rounded-lg border bg-card p-3"
                data-testid={`open-${o.id}`}
              >
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <span className="font-mono text-xs text-muted-foreground">
                    {o.id}
                  </span>
                  <strong>{o.question}</strong>
                  {!reviewing && (
                    <span className="ml-auto text-xs text-muted-foreground">
                      {o.decided_by}
                    </span>
                  )}
                </div>
                {reviewing ? (
                  <>
                    <textarea
                      aria-label={`Answer to ${o.id}: ${o.question}`}
                      rows={2}
                      className="font-sans text-sm"
                      value={openDraft[o.id] ?? ""}
                      onChange={(e) =>
                        setOpenDraft({ ...openDraft, [o.id]: e.target.value })
                      }
                    />
                    <span className="text-xs text-muted-foreground">
                      {o.decided_by.startsWith("architect")
                        ? `Proposed by the architect${o.rationale ? ` — ${o.rationale}` : ""}`
                        : `Answered by ${o.decided_by}`}
                    </span>
                  </>
                ) : (
                  <div className="text-sm">{o.answer}</div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {questions.length > 0 && (
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
                  <span className="ml-auto text-xs text-muted-foreground">
                    {a?.decided_by}
                  </span>
                </div>
                {!reviewing && (
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
                {reviewing && d && (
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

      {(reviewing || discussion.length > 0) && (
        <div
          className="grid gap-2 border-t pt-3"
          data-testid="design-discussion"
        >
          <div className="flex items-center gap-2">
            <MessageCircle className="size-4 text-primary" />
            <strong className="text-sm">Conversation with the architect</strong>
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
                  ` · updated ${(t.files ?? []).join(", ")}`}
              </div>
              {t.text}
            </div>
          ))}
          {v.discuss_pending && (
            <div className="text-sm text-muted-foreground" role="status">
              The architect is reading your input and updating the plan…
            </div>
          )}
          {reviewing && (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send.mutate();
              }}
            >
              <textarea
                aria-label="Comments and questions for the architect"
                rows={3}
                className="font-sans text-sm"
                placeholder="Comments or questions on the spec, plan or choices, e.g. Why admin-created accounts? Members should see each other's books."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
              />
              <div className="flex flex-wrap items-center gap-2">
                <Button disabled={busy || pendingCount === 0}>
                  <Send /> Send to the architect
                  {pendingCount > 0 && ` (${pendingCount})`}
                </Button>
                <Button
                  type="button"
                  variant={pendingCount > 0 ? "outline" : "default"}
                  onClick={() => approve.mutate()}
                  disabled={busy || approve.isPending || pendingCount > 0}
                  title={
                    pendingCount > 0
                      ? "Send your changes first, or undo them"
                      : undefined
                  }
                >
                  <Check /> Approve &amp; build
                </Button>
                <span className="text-xs text-muted-foreground">
                  {pendingCount > 0
                    ? `${pendingCount} change${pendingCount > 1 ? "s" : ""} ready to send together`
                    : "Nothing to send: approving builds what is shown."}
                </span>
              </div>
            </form>
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
      <Problems error={send.error ?? approve.error ?? null} />

      {roadmap.length > 0 && (
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
              {roadmap.map((r) => (
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
