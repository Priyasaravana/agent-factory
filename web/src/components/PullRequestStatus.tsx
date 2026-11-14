import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitPullRequest, MessageSquareReply, RefreshCw, Wrench } from "lucide-react";
import { toast } from "sonner";
import { api, unwrap, type Change, type CiFixView, type FeedbackFixView } from "../api/client";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

const CI: Record<string, [string, string]> = {
  none: ["no CI", "muted"],
  pending: ["CI running", "info"],
  passing: ["CI passing", "ok"],
  failing: ["CI failing", "bad"],
  unknown: ["CI unknown", "warn"],
};
const REVIEW: Record<string, [string, string]> = {
  none: ["no review", "muted"],
  commented: ["comments", "info"],
  approved: ["approved", "ok"],
  changes_requested: ["changes requested", "warn"],
  unknown: ["review unknown", "warn"],
};

/** A change's pull request on the team's repo (J3): where it stands as last read from the host, and a
 * Refresh that reads it again now. Merged or closed shows as the change's outcome. Red CI offers a fix
 * that continues the same pull request (slice 2); the engine decides when (`ci_fix`), the page only shows
 * it. A fix change shows the pull request it continues, and why (red CI, or the team's feedback: slice 3).
 * New feedback is counted here and listed, with its answers, by `PullRequestFeedback`. */
export default function PullRequestStatus({
  change,
  ciFix = null,
  feedback = null,
  canFix = false,
}: {
  change: Change;
  ciFix?: CiFixView | null;
  feedback?: FeedbackFixView | null;
  /** the product's creator or an admin (the engine checks it too) */
  canFix?: boolean;
}) {
  const qc = useQueryClient();
  const pr = change.pull_request;
  const following = change.status === "awaiting_feedback" && !change.outcome && (!pr || pr.state === "open");
  const refresh = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/pull-request/refresh", {
          params: { path: { change_id: change.id } },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["change", change.id] });
      qc.invalidateQueries({ queryKey: ["product", change.product_id] });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const fix = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/ci-fix", {
          params: { path: { change_id: change.id } },
        }),
      ),
    onSuccess: (started) => {
      toast.success(`Fix started: change ${started.id} continues the pull request`);
      qc.invalidateQueries({ queryKey: ["change", change.id] });
      qc.invalidateQueries({ queryKey: ["product", change.product_id] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const cont = change.continues;
  if (cont?.reason === "feedback") {
    const pushed = cont.pushed_sha
      ? `pushed ${cont.from_sha.slice(0, 7)} → ${cont.pushed_sha.slice(0, 7)}`
      : "no code pushed";
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5" id="pull-request" data-testid="feedback-continues">
        <a
          className="pill info inline-flex items-center gap-1"
          href={cont.pr_url}
          target="_blank"
          rel="noreferrer"
          title={[
            `Answers ${(cont.feedback ?? []).length} piece(s) of feedback on ${cont.branch}, from ${cont.from_sha.slice(0, 7)}`,
            `Continues change ${cont.change_id}: same branch, same pull request, fast-forward only`,
            pushed,
          ].join("\n")}
        >
          <MessageSquareReply className="size-3.5" aria-hidden />
          answers #{cont.number} ↗
        </a>
        {cont.auto && <span className="pill muted">started by the lane</span>}
      </span>
    );
  }
  if (cont) {
    const pushed = cont.pushed_sha ? `pushed ${cont.from_sha.slice(0, 7)} → ${cont.pushed_sha.slice(0, 7)}` : "";
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5" id="pull-request" data-testid="ci-fix-continues">
        <a
          className="pill info inline-flex items-center gap-1"
          href={cont.pr_url}
          target="_blank"
          rel="noreferrer"
          title={[
            `Fixes CI (${(cont.failing ?? []).join(", ") || "a check"}) on ${cont.branch}, from ${cont.from_sha.slice(0, 7)}`,
            `Continues change ${cont.change_id}: same branch, same pull request, fast-forward only`,
            pushed,
          ]
            .filter(Boolean)
            .join("\n")}
        >
          <Wrench className="size-3.5" aria-hidden />
          continues #{cont.number} ↗
        </a>
        {cont.auto && <span className="pill muted">started by the lane</span>}
      </span>
    );
  }
  if (!change.pr_url) return null;

  const read = pr ? `Last read ${new Date(pr.checked_at).toLocaleString()} (${pr.source})` : "Not read yet";
  const failing = pr?.failing?.length ? `Failing: ${pr.failing.join(", ")}` : "";
  const unread = pr?.unread?.length ? `Couldn't read: ${pr.unread.join(", ")}` : "";
  const [ci, ciTone] = CI[pr?.checks ?? "none"];
  const [review, reviewTone] = REVIEW[pr?.review ?? "none"];

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" id="pull-request" data-testid="pull-request-status">
      <a
        className="pill ok inline-flex items-center gap-1"
        href={pr?.url ?? change.pr_url}
        target="_blank"
        rel="noreferrer"
        title={[read, failing, unread].filter(Boolean).join("\n")}
      >
        <GitPullRequest className="size-3.5" aria-hidden />
        {pr ? `#${pr.number}` : "pull request"} ↗
      </a>
      {pr?.state === "open" && (
        <>
          <span className={cn("pill", ciTone)} title={[failing, unread].filter(Boolean).join("\n") || undefined}>
            {ci}
          </span>
          <span className={cn("pill", reviewTone)}>{review}</span>
          {pr.unread && pr.unread.length > 0 && (
            <span className="pill warn" title={unread}>
              partly read
            </span>
          )}
        </>
      )}
      {ciFix?.offer && canFix && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2"
          onClick={() => fix.mutate()}
          disabled={fix.isPending}
          title={`${ciFix.reason}. A fix works on the pull request's branch and pushes to it (never forced, never a second pull request).`}
          data-testid="ci-fix-start"
        >
          <Wrench />
          Fix CI
        </Button>
      )}
      {ciFix?.running && (
        <span className="pill info" title={ciFix.reason}>
          fix {ciFix.running.slice(0, 6)} in progress
        </span>
      )}
      {ciFix && !ciFix.offer && !ciFix.running && (ciFix.fixes ?? []).length > 0 && pr?.checks !== "passing" && (
        <span className="pill muted" title={ciFix.reason}>
          {(ciFix.fixes ?? []).length} CI fix{(ciFix.fixes ?? []).length > 1 ? "es" : ""}
        </span>
      )}
      {feedback && (feedback.new ?? []).length > 0 && (
        <a
          href="#pr-feedback"
          className="pill warn no-underline"
          title={feedback.reason}
          data-testid="pr-feedback-count"
        >
          {(feedback.new ?? []).length} new feedback
        </a>
      )}
      {following && (
        <Button
          size="sm"
          variant="ghost"
          className="h-7 px-2"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          title="Read the pull request again: CI, reviews, comments, merged or closed"
          aria-label="Refresh the pull request"
        >
          <RefreshCw className={cn(refresh.isPending && "animate-spin")} />
          Refresh
        </Button>
      )}
    </span>
  );
}
