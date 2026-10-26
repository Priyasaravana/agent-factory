import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Every page's frame: the one <main> landmark, the one <h1>, an optional description and the
 * page's main actions (shown beside the title on wide screens, under it on a phone). */
export function Page({
  title,
  description,
  actions,
  className,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <main
      id="main"
      className={cn("mx-auto grid w-full max-w-5xl gap-6 px-4 py-8 sm:px-6", className)}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="grid min-w-0 gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </main>
  );
}
