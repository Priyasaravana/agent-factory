import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

// A styled native <select>: keyboard, screen reader and mobile pickers work without extra code.
export function Select({ className, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
