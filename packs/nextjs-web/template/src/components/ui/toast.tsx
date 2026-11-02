"use client";

import { Toaster as Sonner } from "sonner";

// Toasts: `import { toast } from "sonner"; toast.success("Saved")` in a client component. The
// <Toaster /> is in the root layout once.
export { toast } from "sonner";

export function Toaster() {
  return (
    <Sonner
      position="bottom-right"
      richColors
      closeButton
      toastOptions={{ classNames: { closeButton: "phone:min-h-[44px] phone:min-w-[44px]" } }}
    />
  );
}
