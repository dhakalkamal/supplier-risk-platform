import * as React from "react";
import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => (
    <textarea
      className={cn(
        "flex min-h-[80px] w-full rounded-md border border-[--color-border] bg-[--color-bg-input]",
        "px-3 py-2 text-sm text-[--color-text-primary] placeholder:text-[--color-text-muted]",
        "focus:outline-none focus:border-[--color-border-focus]",
        "disabled:cursor-not-allowed disabled:opacity-50 resize-none",
        "transition-colors",
        className,
      )}
      ref={ref}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export { Textarea };
