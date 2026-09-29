import { AlertTriangle, Ban, CheckCircle2, CircleDashed, Loader2, MessageCircleQuestion, PauseCircle, XCircle } from "lucide-react";
import type { ReactNode } from "react";

const TONE: Record<string, string> = {
  queued: "info",
  running: "info",
  needs_input: "warn",
  paused_limits: "warn",
  held: "bad",
  interrupted: "warn",
  awaiting_feedback: "ok",
  cancelled: "muted",
  failed: "bad",
};

const LABEL: Record<string, string> = {
  needs_input: "needs your answers",
  paused_limits: "paused (usage limit)",
  awaiting_feedback: "delivered · awaiting feedback",
};

const ICON: Record<string, ReactNode> = {
  queued: <CircleDashed />,
  running: <Loader2 className="animate-spin" />,
  needs_input: <MessageCircleQuestion />,
  paused_limits: <PauseCircle />,
  held: <AlertTriangle />,
  interrupted: <PauseCircle />,
  awaiting_feedback: <CheckCircle2 />,
  cancelled: <Ban />,
  failed: <XCircle />,
};

export default function StatusPill({ status }: { status: string }) {
  return (
    <span className={`pill ${TONE[status] ?? "muted"} [&_svg]:size-3`}>
      {ICON[status]}
      {LABEL[status] ?? status}
    </span>
  );
}
