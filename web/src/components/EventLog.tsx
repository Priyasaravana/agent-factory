import { Activity, AlertTriangle, ArrowDown, ArrowUp, Bug, ChevronRight, CircleAlert, Info, Pause, Play } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FactoryEvent } from "../api/client";
import { cn } from "../lib/utils";
import { Panel } from "./ui/panel";

type Level = "error" | "warn" | "info" | "debug";
const levelOf = (e: FactoryEvent): Level => (e.level as Level | undefined) ?? "info";
const LEVEL: Record<Level, { label: string; icon: typeof Info; tone: string }> = {
  error: { label: "error", icon: CircleAlert, tone: "text-bad" },
  warn: { label: "warning", icon: AlertTriangle, tone: "text-warn" },
  info: { label: "info", icon: Info, tone: "text-info" },
  debug: { label: "detail", icon: Bug, tone: "text-muted-foreground" },
};

/** What a person filters by (2026-11-06, Saravana): pick any number; none picked, or all picked, is
 * everything. "Problems" is by level (errors and warnings); the rest by what kind of event it is. */
const FILTERS = [
  { id: "problems", label: "Problems", match: (e: FactoryEvent) => ["error", "warn"].includes(levelOf(e)) },
  { id: "decisions", label: "Decisions", match: (e: FactoryEvent) => e.kind === "decision" },
  {
    id: "stations",
    label: "Stations",
    match: (e: FactoryEvent) => e.kind.startsWith("station") || e.kind === "status" || e.kind === "feedback",
  },
  { id: "commands", label: "Commands", match: (e: FactoryEvent) => e.kind === "command" },
  { id: "agents", label: "Agents", match: (e: FactoryEvent) => e.kind === "agent" },
  { id: "tools", label: "Tool calls", match: (e: FactoryEvent) => e.kind === "log" },
] as const;
type FilterId = (typeof FILTERS)[number]["id"];

/** The one line an event shows folded: its first line, the rest unfolds. */
function firstLine(message: string): { head: string; more: boolean } {
  const text = message.trim();
  const nl = text.indexOf("\n");
  const head = nl === -1 ? text : text.slice(0, nl);
  return { head, more: nl !== -1 || head.length > 40 };
}

/** A problem stays open until the person folds it: those are the lines people come to read. */
const openByDefault = (e: FactoryEvent) => levelOf(e) === "error" || (e.kind === "status" && /held|failed/i.test(e.message));

/** A change's activity (2026-11-06, Saravana): one line per event, unfolded on a click; follows new
 * events only while the reader is at the live end, and stops the moment they scroll away or open a
 * line, with a "new events" button to jump back; multi-select filters where none or all means all. */
export default function EventLog({ events, live }: { events: FactoryEvent[]; live: boolean }) {
  const [picked, setPicked] = useState<Set<FilterId>>(new Set());
  const [newestFirst, setNewestFirst] = useState(true);
  const [query, setQuery] = useState("");
  const [toggled, setToggled] = useState<Set<number>>(new Set()); // flipped from their default
  const [following, setFollowing] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const seen = useRef(events.length);

  const all = picked.size === 0 || picked.size === FILTERS.length;
  const q = query.trim().toLowerCase();
  const matching = events.filter(
    (e) =>
      (all || FILTERS.some((f) => picked.has(f.id) && f.match(e))) &&
      (!q || `${e.message} ${e.station ?? ""} ${e.kind}`.toLowerCase().includes(q)),
  );
  const shown = newestFirst ? [...matching].reverse() : matching;
  const counts = Object.fromEntries(FILTERS.map((f) => [f.id, events.filter(f.match).length])) as Record<
    FilterId,
    number
  >;
  const errors = events.filter((e) => levelOf(e) === "error").length;
  const warnings = events.filter((e) => levelOf(e) === "warn").length;

  const atLiveEnd = useCallback(() => {
    const el = box.current;
    if (!el) return true;
    return newestFirst ? el.scrollTop <= 8 : el.scrollHeight - el.scrollTop - el.clientHeight <= 8;
  }, [newestFirst]);
  const jump = useCallback(() => {
    const el = box.current;
    if (el) el.scrollTop = newestFirst ? 0 : el.scrollHeight;
    setFollowing(true);
    setUnseen(0);
  }, [newestFirst]);

  // new events: follow only while following; otherwise count them for the jump button
  useLayoutEffect(() => {
    const added = events.length - seen.current;
    seen.current = events.length;
    if (added <= 0) return;
    if (following) {
      const el = box.current;
      if (el) el.scrollTop = newestFirst ? 0 : el.scrollHeight;
    } else {
      setUnseen((n) => n + added);
    }
  }, [events.length, following, newestFirst]);
  useEffect(() => jump(), [newestFirst, jump]);

  const onScroll = () => {
    const end = atLiveEnd();
    if (end && !following) {
      setFollowing(true);
      setUnseen(0);
    } else if (!end && following) {
      setFollowing(false);
    }
  };
  const flip = (id: number) => {
    setFollowing(false); // reading a line: stop moving it
    setToggled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const pick = (id: FilterId) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next.size === FILTERS.length ? new Set() : next; // all picked is all
    });

  return (
    <Panel
      id="activity"
      data-testid="activity"
      icon={<Activity className="text-primary" />}
      title={
        <>
          Activity
          {live && (
            <span className="relative flex size-2" title="Live">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-ok opacity-60" />
              <span className="relative inline-flex size-2 rounded-full bg-ok" />
            </span>
          )}
        </>
      }
      summary={
        <>
          <span data-testid="activity-total">{events.length} events</span>
          {errors > 0 && (
            <span className="pill bad" data-testid="activity-error-count">
              <CircleAlert /> {errors} {errors === 1 ? "error" : "errors"}
            </span>
          )}
          {warnings > 0 && (
            <span className="pill warn" data-testid="activity-warn-count">
              <AlertTriangle /> {warnings} {warnings === 1 ? "warning" : "warnings"}
            </span>
          )}
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Show">
          <button
            type="button"
            className={cn("chip", all && "active")}
            aria-pressed={all}
            onClick={() => setPicked(new Set())}
            data-testid="filter-all"
          >
            All <span className="tabular-nums text-muted-foreground">{events.length}</span>
          </button>
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={cn("chip", !all && picked.has(f.id) && "active")}
              aria-pressed={!all && picked.has(f.id)}
              onClick={() => pick(f.id)}
              data-testid={`filter-${f.id}`}
            >
              {f.label} <span className="tabular-nums text-muted-foreground">{counts[f.id]}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          aria-label="Search activity"
          placeholder="Search activity…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="h-8 w-full text-sm sm:ml-auto sm:w-56"
        />
        <span className="text-xs text-muted-foreground" data-testid="activity-count">
          {matching.length === events.length ? `${events.length} events` : `${matching.length} of ${events.length} events`}
        </span>
        <button
          type="button"
          className="tab inline-flex items-center gap-1"
          onClick={() => (following ? setFollowing(false) : jump())}
          aria-pressed={following}
          title={following ? "Stop following new events" : "Follow new events"}
          data-testid="activity-follow"
        >
          {following ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          {following ? "Following" : "Paused"}
        </button>
        <button
          type="button"
          className="tab"
          onClick={() => setNewestFirst(!newestFirst)}
          title="Change the order"
          aria-label={newestFirst ? "Newest first" : "Oldest first"}
        >
          {newestFirst ? "newest first" : "oldest first"}
        </button>
      </div>

      <div className="relative">
        <div className="log" ref={box} onScroll={onScroll} data-testid="activity-log">
          {shown.map((e) => {
            const l = levelOf(e);
            const Icon = LEVEL[l].icon;
            const output = typeof e.data?.output === "string" ? (e.data.output as string) : "";
            const evidence = typeof e.data?.evidence === "string" ? (e.data.evidence as string) : "";
            const { head, more } = firstLine(e.message);
            const foldable = more || Boolean(output || evidence);
            const open = foldable && openByDefault(e) !== toggled.has(e.id);
            return (
              <div key={e.id} className={cn("ev", `ev-${e.kind}`, `lv-${l}`, open && "open")} data-level={l}>
                <span className="ts" title={new Date(e.ts).toLocaleString()}>
                  {new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
                </span>
                <span className="st">{e.station ?? ""}</span>
                {foldable ? (
                  <button
                    type="button"
                    className="msg ev-line"
                    aria-expanded={open}
                    onClick={() => flip(e.id)}
                    title={open ? "Fold" : "Show all"}
                  >
                    <ChevronRight className={cn("ev-chev", open && "rotate-90")} aria-hidden />
                    <Icon className={cn("lv-icon", LEVEL[l].tone)} aria-label={LEVEL[l].label} />
                    <span className={cn(!open && "truncate")}>{head}</span>
                  </button>
                ) : (
                  <span className="msg pl-[18px]">
                    <Icon className={cn("lv-icon", LEVEL[l].tone)} aria-label={LEVEL[l].label} />
                    {head}
                  </span>
                )}
                {open && (
                  <div className="ev-more">
                    {more && <p className="m-0 whitespace-pre-wrap break-words">{e.message}</p>}
                    {(output || evidence) && <pre className="pre">{output || evidence}</pre>}
                  </div>
                )}
              </div>
            );
          })}
          {shown.length === 0 && (
            <p className="m-0 p-3 text-muted-foreground">
              {q ? `Nothing matches “${query.trim()}”.` : events.length ? "Nothing of the kinds picked." : "Nothing yet."}
            </p>
          )}
        </div>
        {!following && unseen > 0 && (
          <button
            type="button"
            className={cn(
              "absolute left-1/2 -translate-x-1/2 rounded-full border bg-card px-3 py-1 text-xs shadow-sm",
              newestFirst ? "top-2" : "bottom-2",
            )}
            onClick={jump}
            data-testid="activity-jump"
          >
            {newestFirst ? <ArrowUp className="mr-1 inline size-3.5" /> : <ArrowDown className="mr-1 inline size-3.5" />}
            {unseen} new {unseen === 1 ? "event" : "events"}, jump to latest
          </button>
        )}
      </div>
    </Panel>
  );
}
