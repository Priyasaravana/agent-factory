import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, ExternalLink, FileCode2, MessageSquare, MessageSquareReply } from "lucide-react";
import { toast } from "sonner";
import { api, unwrap, type Change, type FeedbackFixView, type FeedbackResponse, type PrFeedback } from "../api/client";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

const KIND_ICON = { review: ClipboardCheck, inline: FileCode2, comment: MessageSquare } as const;
const ACTION: Record<FeedbackResponse["action"], [string, string]> = {
  addressed: ["addressed", "ok"],
  answered: ["answered", "info"],
  declined: ["not changed", "muted"],
};

function where(f: PrFeedback): string {
  if (f.kind === "inline") return `${f.path || "a file"}${f.line ? `:${f.line}` : ""}`;
  if (f.kind === "review") return `review${f.state ? ` · ${f.state.replace("_", " ")}` : ""}`;
  return "conversation";
}

function Row({
  f,
  response,
  status,
}: {
  f: PrFeedback;
  response?: FeedbackResponse;
  status: [string, string];
}) {
  const Icon = KIND_ICON[f.kind];
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 py-2" data-testid="pr-feedback-item">
      <Icon className="mt-0.5 size-4 text-muted-foreground" aria-hidden />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-medium">@{f.author || "someone"}</span>
          <span className="truncate text-xs text-muted-foreground">{where(f)}</span>
          {f.url && (
            <a
              href={f.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center text-xs text-muted-foreground"
              aria-label="Open on the repo's host"
            >
              <ExternalLink className="size-3" />
            </a>
          )}
        </div>
        <p className="m-0 line-clamp-2 whitespace-pre-line text-sm text-muted-foreground" title={f.body}>
          {f.body}
        </p>
        {response && (
          <p className="m-0 mt-1 flex items-start gap-1.5 text-sm">
            <MessageSquareReply className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
            <span className="line-clamp-3" title={response.reply}>
              {response.reply}
            </span>
          </p>
        )}
      </div>
      <span className={cn("pill shrink-0", status[1])}>{status[0]}</span>
    </li>
  );
}

/** The team's feedback on a change's pull request (J3 slice 3): what is new, what was answered and how,
 * and what came from outside the team (shown, never acted on). On a change that answers feedback, the
 * feedback it was given and its responses. The engine decides what is offered; the page only shows it. */
export default function PullRequestFeedback({
  change,
  feedback = null,
  canAnswer = false,
}: {
  change: Change;
  feedback?: FeedbackFixView | null;
  /** the product's creator or an admin (the engine checks it too) */
  canAnswer?: boolean;
}) {
  const qc = useQueryClient();
  const answer = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/address-feedback", {
          params: { path: { change_id: change.id } },
        }),
      ),
    onSuccess: (started) => {
      toast.success(`Change ${started.id} answers the feedback on the same pull request`);
      qc.invalidateQueries({ queryKey: ["change", change.id] });
      qc.invalidateQueries({ queryKey: ["product", change.product_id] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const cont = change.continues;
  if (cont?.reason === "feedback") {
    const responses = new Map((cont.responses ?? []).map((r) => [r.id, r]));
    return (
      <section className="rounded-lg border p-3" data-testid="pr-feedback-answering">
        <h4 className="m-0 text-sm font-medium">
          Feedback this change answers on #{cont.number}
        </h4>
        <ul className="m-0 list-none divide-y p-0">
          {(cont.feedback ?? []).map((f) => {
            const r = responses.get(f.id);
            const status: [string, string] = r
              ? [r.posted ? ACTION[r.action][0] : `${ACTION[r.action][0]} · not posted`, ACTION[r.action][1]]
              : ["to answer", "warn"];
            return <Row key={f.id} f={f} response={r} status={status} />;
          })}
        </ul>
      </section>
    );
  }
  if (!feedback) return null;
  const { new: fresh = [], outside = [], answered = [] } = feedback;
  if (!fresh.length && !outside.length && !answered.length && !feedback.running) return null;

  return (
    <section className="rounded-lg border p-3" id="pr-feedback" data-testid="pr-feedback">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="m-0 text-sm font-medium">Feedback on the pull request</h4>
        {fresh.length > 0 && <span className="pill warn">{fresh.length} new</span>}
        {answered.length > 0 && <span className="pill ok">{answered.length} answered</span>}
        {feedback.mode === "autonomous" && <span className="pill muted">answered by the lane</span>}
        <span className="ml-auto" />
        {feedback.offer && canAnswer && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2"
            onClick={() => answer.mutate()}
            disabled={answer.isPending}
            title={`${feedback.reason}. A change answers or addresses each piece on the same pull request and replies there.`}
            data-testid="pr-feedback-start"
          >
            <MessageSquareReply />
            Answer feedback
          </Button>
        )}
        {feedback.running && (
          <span className="pill info" title={feedback.reason}>
            change {feedback.running.slice(0, 6)} in progress
          </span>
        )}
      </div>
      <ul className="m-0 list-none divide-y p-0">
        {fresh.map((f) => (
          <Row key={f.id} f={f} status={["new", "warn"]} />
        ))}
        {answered.map((a) => (
          <Row
            key={a.feedback.id}
            f={a.feedback}
            response={a.response}
            status={ACTION[a.response.action]}
          />
        ))}
        {outside.map((f) => (
          <Row key={f.id} f={f} status={["outside the team", "muted"]} />
        ))}
      </ul>
      {outside.length > 0 && (
        <p className="m-0 mt-1 text-xs text-muted-foreground">
          Feedback from people without write access to the repo is shown here but never given to the factory's
          agents. Copy what you agree with into a review or comment of your own.
        </p>
      )}
    </section>
  );
}
