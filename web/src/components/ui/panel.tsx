import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A collapsible panel on the product and change pages.
 *
 * - Closed, it keeps a one-line summary, so a long page reads as a list of headlines.
 * - Open or closed is remembered per browser and per kind of panel (`id`), except that a panel that
 *   needs the person (`attention`) always opens: nobody should have to hunt for it.
 * - "Expand all" / "Collapse all" and the Needs-you panel's "Go to" reach every panel through
 *   window events (`openPanel`, `setAllPanels`), so panels stay independent of each other.
 */
export type PanelTone = "default" | "attention" | "ok" | "bad";

const KEY = (id: string) => `af:panel:${id}`;
const OPEN_ONE = "af:open-panel";
const SET_ALL = "af:panels";

function load(id: string): boolean | null {
  try {
    const v = window.localStorage.getItem(KEY(id));
    return v === null ? null : v === "1";
  } catch {
    return null;
  }
}

function save(id: string, open: boolean) {
  try {
    window.localStorage.setItem(KEY(id), open ? "1" : "0");
  } catch {
    /* private window or blocked storage: the panel still works, it just doesn't remember */
  }
}

/** Open the panel with this id (or element id) and scroll it into view. */
export function openPanel(id: string) {
  window.dispatchEvent(new CustomEvent(OPEN_ONE, { detail: id }));
  // after React has opened it
  window.setTimeout(() => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      el.querySelector<HTMLElement>("[data-panel-toggle]")?.focus({ preventScroll: true });
    }
  }, 60);
}

/** Open or close every panel on the page. */
export function setAllPanels(open: boolean) {
  window.dispatchEvent(new CustomEvent(SET_ALL, { detail: open }));
}

export function Panel({
  id,
  title,
  summary,
  icon,
  tone = "default",
  attention = false,
  defaultOpen = false,
  right,
  className,
  bodyClassName,
  children,
  ...rest
}: {
  id: string;
  title: ReactNode;
  summary?: ReactNode;
  icon?: ReactNode;
  tone?: PanelTone;
  attention?: boolean;
  defaultOpen?: boolean;
  right?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
  "data-testid"?: string;
  "aria-label"?: string;
}) {
  const [open, setOpenState] = useState<boolean>(() => attention || (load(id) ?? defaultOpen));
  const bodyId = useId();
  const setOpen = useCallback(
    (v: boolean) => {
      setOpenState(v);
      save(id, v);
    },
    [id],
  );
  // a panel that starts needing the person opens by itself
  useEffect(() => {
    if (attention) setOpenState(true);
  }, [attention]);
  useEffect(() => {
    const one = (e: Event) => {
      if ((e as CustomEvent<string>).detail === id) setOpenState(true);
    };
    const all = (e: Event) => setOpen((e as CustomEvent<boolean>).detail);
    window.addEventListener(OPEN_ONE, one);
    window.addEventListener(SET_ALL, all);
    return () => {
      window.removeEventListener(OPEN_ONE, one);
      window.removeEventListener(SET_ALL, all);
    };
  }, [id, setOpen]);

  return (
    <section
      id={id}
      data-testid={rest["data-testid"]}
      aria-label={rest["aria-label"]}
      data-open={open ? "true" : "false"}
      className={cn(
        "scroll-mt-32 rounded-xl border bg-card",
        tone === "attention" && "border-warn/60 bg-warn/5 ring-1 ring-warn/20",
        tone === "bad" && "border-bad/50",
        tone === "ok" && "border-ok/40",
        className,
      )}
    >
      <div className="flex items-start gap-2 p-3 sm:px-4">
        <button
          type="button"
          data-panel-toggle
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen(!open)}
          className="flex min-w-0 flex-1 items-start gap-2 whitespace-normal rounded-md bg-transparent p-0 text-left font-normal text-foreground shadow-none hover:bg-transparent hover:brightness-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <ChevronRight
            aria-hidden
            className={cn(
              "mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
          {icon && <span className="mt-0.5 shrink-0 text-muted-foreground [&_svg]:size-4">{icon}</span>}
          <span className="grid min-w-0 flex-1 gap-0.5">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold">{title}</span>
            {summary && <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">{summary}</span>}
          </span>
        </button>
        {right && <div className="flex shrink-0 flex-wrap items-center gap-2">{right}</div>}
      </div>
      {open && (
        <div id={bodyId} className={cn("grid gap-3 border-t px-3 pb-4 pt-3 sm:px-4", bodyClassName)}>
          {children}
        </div>
      )}
    </section>
  );
}
