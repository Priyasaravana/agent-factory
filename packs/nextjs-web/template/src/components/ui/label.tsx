import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

// Every form control has a label: <Label htmlFor="email">Email</Label><Input id="email" … />
export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-sm leading-none font-medium", className)} {...props} />;
}
