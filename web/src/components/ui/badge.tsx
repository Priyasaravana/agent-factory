import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva("pill", {
  variants: {
    tone: { ok: "ok", warn: "warn", bad: "bad", info: "info", muted: "muted", primary: "text-primary" },
  },
  defaultVariants: { tone: "muted" },
});

export function Badge({
  className,
  tone,
  ...p
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...p} />;
}
