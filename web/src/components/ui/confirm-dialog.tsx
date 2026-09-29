import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { Button } from "./button";

/** A small, accessible confirmation dialog (Radix) for consequential actions. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  children,
  confirm,
  onConfirm,
  busy,
  destructive,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  children: ReactNode;
  confirm: string;
  onConfirm: () => void;
  busy?: boolean;
  destructive?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 grid w-[min(92vw,28rem)] -translate-x-1/2 -translate-y-1/2 gap-3 rounded-xl border bg-card p-5 shadow-2xl">
          <Dialog.Title className="m-0 text-base font-semibold">{title}</Dialog.Title>
          <Dialog.Description asChild>
            <div className="grid gap-2 text-sm text-muted-foreground">{children}</div>
          </Dialog.Description>
          <div className="mt-2 flex justify-end gap-2">
            <Dialog.Close asChild>
              <Button variant="outline">Cancel</Button>
            </Dialog.Close>
            <Button variant={destructive ? "destructive" : "default"} onClick={onConfirm} disabled={busy}>
              {busy ? "Working…" : confirm}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
