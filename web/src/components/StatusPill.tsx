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

export default function StatusPill({ status }: { status: string }) {
  return <span className={`pill ${TONE[status] ?? "muted"}`}>{LABEL[status] ?? status}</span>;
}
