import { AppWindow, Bot, Cog, FileText, Layers, LayoutPanelTop, Server, type LucideIcon } from "lucide-react";
import type { components } from "../api/schema";
import { cn } from "../lib/utils";

export type StackCard = components["schemas"]["StackCard"];

/** What each kind of stack builds (ADR-0041 §1 `builds`), in words a requester knows. */
export const BUILDS: Record<string, { label: string; icon: LucideIcon }> = {
  service: { label: "API service", icon: Server },
  "web-app": { label: "Web app", icon: AppWindow },
  spa: { label: "Single-page app", icon: LayoutPanelTop },
  "full-stack": { label: "Full-stack app", icon: Layers },
  "static-site": { label: "Static site", icon: FileText },
  "agent-service": { label: "Agent service", icon: Bot },
  worker: { label: "Background worker", icon: Cog },
};

export function buildsOf(card: StackCard) {
  return BUILDS[card.builds ?? ""] ?? { label: "App", icon: Layers };
}

const SHOWN = 4;

/** "What are you building?": one card per installed stack (ADR-0041 §2). */
export default function StackCards({
  cards,
  value,
  onChange,
}: {
  cards: StackCard[];
  value: string;
  onChange: (blueprint: string) => void;
}) {
  return (
    <div className="grid gap-1.5">
      <span className="text-sm font-medium" id="stack-label">
        What are you building?
      </span>
      <div role="radiogroup" aria-labelledby="stack-label" className="grid gap-2">
        {cards.map((c) => {
          const { label, icon: Icon } = buildsOf(c);
          const on = c.blueprint === value;
          const tech = c.technologies ?? [];
          return (
            <button
              key={c.blueprint}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(c.blueprint)}
              className={cn(
                // a card, not a button: undo the base button look (nowrap, primary colours, centring)
                "flex h-auto w-full items-start justify-start gap-3 whitespace-normal rounded-xl border bg-card p-3",
                "text-left font-normal text-foreground shadow-none transition-colors hover:brightness-100",
                "hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                on ? "border-primary bg-primary/5 ring-1 ring-primary" : "",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "grid size-9 shrink-0 place-items-center rounded-lg",
                  on ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                )}
              >
                <Icon className="size-4" />
              </span>
              <span className="grid min-w-0 flex-1 gap-1">
                <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <span className="font-semibold">{c.title}</span>
                  <span className="text-xs text-muted-foreground">{label}</span>
                </span>
                <span className="text-xs leading-snug text-muted-foreground">{c.summary}</span>
                {tech.length > 0 && (
                  <span className="flex flex-wrap gap-1 pt-0.5">
                    {tech.slice(0, SHOWN).map((t) => (
                      <span key={t} className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                        {t}
                      </span>
                    ))}
                    {tech.length > SHOWN && (
                      <span className="px-1 py-0.5 text-[11px] text-muted-foreground" title={tech.slice(SHOWN).join(", ")}>
                        +{tech.length - SHOWN}
                      </span>
                    )}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
