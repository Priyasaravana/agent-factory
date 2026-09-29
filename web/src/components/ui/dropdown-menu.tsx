import * as DM from "@radix-ui/react-dropdown-menu";
import * as React from "react";
import { cn } from "@/lib/utils";

export const DropdownMenu = DM.Root;
export const DropdownMenuTrigger = DM.Trigger;
export const DropdownMenuSeparator = ({ className, ...p }: DM.DropdownMenuSeparatorProps) => (
  <DM.Separator className={cn("-mx-1 my-1 h-px bg-border", className)} {...p} />
);
export const DropdownMenuLabel = ({ className, ...p }: DM.DropdownMenuLabelProps) => (
  <DM.Label className={cn("px-2 py-1.5 text-xs text-muted-foreground", className)} {...p} />
);
export const DropdownMenuContent = React.forwardRef<HTMLDivElement, DM.DropdownMenuContentProps>(
  ({ className, sideOffset = 6, ...p }, ref) => (
    <DM.Portal>
      <DM.Content
        ref={ref}
        sideOffset={sideOffset}
        className={cn("z-50 min-w-48 rounded-lg border bg-popover p-1 text-foreground shadow-xl", className)}
        {...p}
      />
    </DM.Portal>
  ),
);
DropdownMenuContent.displayName = "DropdownMenuContent";
export const DropdownMenuItem = React.forwardRef<HTMLDivElement, DM.DropdownMenuItemProps>(({ className, ...p }, ref) => (
  <DM.Item
    ref={ref}
    className={cn(
      "flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted [&_svg]:size-4",
      className,
    )}
    {...p}
  />
));
DropdownMenuItem.displayName = "DropdownMenuItem";
