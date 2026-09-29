import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = T.Provider;

export function Tip({ label, children, side = "right" }: { label: ReactNode; children: ReactNode; side?: "right" | "top" | "bottom" | "left" }) {
  return (
    <T.Root delayDuration={200}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content side={side} sideOffset={8} className="z-50 rounded-md border bg-popover px-2 py-1 text-xs shadow-lg">
          {label}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
