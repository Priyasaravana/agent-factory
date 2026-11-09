import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitPullRequest, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, unwrap, type Change } from "../api/client";
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
 * Refresh that reads it again now. Merged or closed shows as the change's outcome. */
export default function PullRequestStatus({ change }: { change: Change }) {
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
  if (!change.pr_url) return null;

  const read = pr ? `Last read ${new Date(pr.checked_at).toLocaleString()} (${pr.source})` : "Not read yet";
  const failing = pr?.failing?.length ? `Failing: ${pr.failing.join(", ")}` : "";
  const unread = pr?.unread?.length ? `Couldn't read: ${pr.unread.join(", ")}` : "";
  const [ci, ciTone] = CI[pr?.checks ?? "none"];
  const [review, reviewTone] = REVIEW[pr?.review ?? "none"];

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-testid="pull-request-status">
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
      {following && (
        <Button
          size="sm"
          variant="ghost"
          className="h-7 px-2"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          title="Read the pull request again: CI, reviews, merged or closed"
          aria-label="Refresh the pull request"
        >
          <RefreshCw className={cn(refresh.isPending && "animate-spin")} />
          Refresh
        </Button>
      )}
    </span>
  );
}
